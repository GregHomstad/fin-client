"""Local dashboard: JSON API + static UI. Binds to 127.0.0.1 only.

    fin-client dashboard   ->  http://127.0.0.1:8780

Safety for a page that shows your finances:
  * loopback only (the CLI refuses any other host);
  * Host header must be loopback (blocks DNS-rebinding);
  * state-changing requests need a custom header and a same-origin Origin (blocks cross-site forms);
  * no third-party requests: strict CSP, system fonts, hand-drawn charts;
  * responses are never cached.
"""
import json
from datetime import date
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from fin_client import accounts, analytics, goals, labeler, recurring, report, snapshot, store
from fin_client import advisor as adv
from fin_client.advisor import DEFAULT_MODEL, Advisor, ollama_url
from fin_client.categorize import categorize, load_rules
from fin_client.importers import import_file
from fin_client.paths import data_dir

STATIC = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
MAX_UPLOAD = 10 * 1024 * 1024
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").endswith("]") \
            else request.headers["host"]
        if host not in LOOPBACK:
            return err("bad host", 403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("x-fin-client") != "1":
                return err("missing X-Fin-Client header", 403)
            if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0] not in LOOPBACK:
                return err("cross-origin request refused", 403)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp


async def body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _ctx():
    """Connection, transactions, rules, accounts and the effective profile (profile.json + account balances)."""
    conn = store.connect()
    accts = accounts.list_accounts(conn)
    txns = store.all_transactions(conn)
    rules = load_rules()
    profile = accounts.effective_profile(snapshot.load_profile(), accts)
    return conn, txns, rules, accts, profile


def _month(m: analytics.Month) -> dict:
    return {"month": m.month, "income": float(m.income), "spending": float(m.spending), "net": float(m.net),
            "savings_rate": None if m.savings_rate is None else float(m.savings_rate),
            "by_category": {k: float(v) for k, v in sorted(m.by_category.items(), key=lambda kv: -kv[1])}}


async def overview(request: Request):
    conn, txns, rules, accts, profile = _ctx()
    months = analytics.cashflow(txns, rules)
    snap = snapshot.build(txns, rules, profile, conn)
    unc = labeler.uncategorized_merchants(txns, rules)
    return ok({
        "accounts": accts, "net_worth": accounts.net_worth(accts),
        "months": [_month(m) for m in months],
        "snapshot": None if "error" in snap else snap,
        "recurring": [{"merchant": r.merchant, "cadence": r.cadence, "amount": float(r.amount),
                       "count": r.count, "last": r.last_date.isoformat(), "next": r.next_expected.isoformat(),
                       "monthly_cost": float(r.monthly_cost)} for r in recurring.detect(txns)],
        "uncategorized": {"merchants": len(unc), "spending": float(sum(u[2] for u in unc))},
        "types": [{"value": t, "label": accounts.LABELS[t], "kind": "liability" if t in accounts.LIABILITY_TYPES else "asset"}
                  for t in accounts.ALL_TYPES],
        "has_data": bool(txns),
    })


async def list_accounts(request: Request):
    conn = store.connect()
    accts = accounts.list_accounts(conn)
    return ok({"accounts": accts, "net_worth": accounts.net_worth(accts)})


async def save_account(request: Request):
    d = await body(request)
    try:
        accounts.upsert(store.connect(), str(d.get("name", "")), str(d.get("type", "")), d.get("balance"),
                        d.get("apr_pct"), d.get("min_payment"), None, str(d.get("notes", "")))
    except ValueError as e:
        return err(str(e))
    return await list_accounts(request)


async def transactions(request: Request):
    q = request.query_params
    conn, txns, rules, _, _ = _ctx()
    needle, month, acct, cat = (q.get("q") or "").upper(), q.get("month"), q.get("account"), q.get("category")
    limit = min(int(q.get("limit", 300)), 2000)
    rows = []
    for t in reversed(txns):
        c = categorize(t.description, t.amount, rules, t.bank_category)
        if (month and not t.date.isoformat().startswith(month)) or (acct and t.account != acct) \
                or (cat and c != cat) or (needle and needle not in t.description.upper()):
            continue
        rows.append({"date": t.date.isoformat(), "description": t.description, "amount": float(t.amount),
                     "account": t.account, "category": c, "internal": t.bank_category == "Transfer (matched)"})
        if len(rows) >= limit:
            break
    cats = sorted({categorize(t.description, t.amount, rules, t.bank_category) for t in txns})
    return ok({"transactions": rows, "categories": cats})


async def uncategorized(request: Request):
    _, txns, rules, _, _ = _ctx()
    return ok({"merchants": [{"description": d, "count": n, "total": float(t)}
                             for d, n, t in labeler.uncategorized_merchants(txns, rules)][:100],
               "standard": labeler.STANDARD})


async def add_rule(request: Request):
    d = await body(request)
    kw, cat = str(d.get("keyword", "")).strip(), str(d.get("category", "")).strip()
    if not kw or not cat:
        return err("keyword and category are required")
    labeler.save_rule(kw, cat)
    return ok({"saved": True})


async def profile_get(request: Request):
    conn = store.connect()
    stored = snapshot.load_profile()
    return ok({"profile": stored, "effective": accounts.effective_profile(stored, accounts.list_accounts(conn))})


async def profile_put(request: Request):
    d = await body(request)
    clean = {k: v for k, v in d.items() if v not in (None, "")}
    (data_dir() / "profile.json").write_text(json.dumps(clean, indent=1), encoding="utf-8")
    return ok({"saved": True})


async def import_upload(request: Request):
    name = Path(request.query_params.get("filename", "upload.csv")).name
    suffix = Path(name).suffix.lower()
    if suffix not in (".csv", ".ofx", ".qfx"):
        return err("Only .csv, .ofx and .qfx files can be imported")
    raw = await request.body()
    if not raw or len(raw) > MAX_UPLOAD:
        return err("File is empty or larger than 10 MB")
    label = (request.query_params.get("account") or "").strip() or "default"
    tmp = data_dir() / "tmp"
    tmp.mkdir(exist_ok=True)
    path = tmp / f"upload{suffix}"
    try:
        path.write_bytes(raw)
        txns = import_file(path, label)
    except (ValueError, OSError) as e:
        return err(f"Could not read that file: {type(e).__name__} (check it is a bank export)")
    finally:
        path.unlink(missing_ok=True)
    conn = store.connect()
    before = {a["name"] for a in accounts.list_accounts(conn)}
    added = store.add(conn, txns)
    accounts.ensure_accounts(conn)
    wanted = request.query_params.get("type")
    if wanted in accounts.ALL_TYPES:  # set the type on accounts this import created
        for a in accounts.list_accounts(conn):
            if a["name"] not in before:
                accounts.upsert(conn, a["name"], wanted, a["balance"], a["apr_pct"], a["min_payment"], a["as_of"], a["notes"])
    return ok({"parsed": len(txns), "added": added})


async def report_pdf(request: Request):
    conn, txns, rules, _, profile = _ctx()
    snap = snapshot.build(txns, rules, profile, conn)
    if "error" in snap:
        return err(snap["error"])
    out = report.build_pdf(snap, data_dir() / "reports" / f"fin-client-report-{date.today():%Y-%m-%d}.pdf")
    return FileResponse(out, media_type="application/pdf", filename=out.name)


async def ollama_models(request: Request):
    import urllib.request
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=2) as r:
            return ok({"running": True, "models": [m["name"] for m in json.load(r).get("models", [])]})
    except Exception:  # noqa: BLE001
        return ok({"running": False, "models": []})


def _advise_snapshot():
    conn, txns, rules, _, profile = _ctx()
    return snapshot.build(txns, rules, profile, conn)


def _claude_payload(d: dict, snap: dict) -> dict:
    return adv.build_claude_payload(snap, d.get("history", []), str(d.get("question", "")).strip(),
                                    bool(d.get("include_merchants")), d.get("model") or None)


async def claude_status(request: Request):
    import importlib.util
    return ok({"ready": adv.claude_ready(), "sdk": importlib.util.find_spec("anthropic") is not None,
               "model": adv.CLAUDE_MODEL})


async def advise_preview(request: Request):
    """Exactly what would be sent to Anthropic for this question. Nothing is sent by this call."""
    d = await body(request)
    if not str(d.get("question", "")).strip():
        return err("question is required")
    snap = _advise_snapshot()
    if "error" in snap:
        return err(snap["error"])
    payload = _claude_payload(d, snap)
    return ok({"payload": payload, "text": adv.render_payload(payload), "digest": adv.payload_digest(payload),
               "approx_input_tokens": len(json.dumps(payload)) // 4, "ready": adv.claude_ready()})


async def advise(request: Request):
    d = await body(request)
    question = str(d.get("question", "")).strip()
    if not question:
        return err("question is required")
    snap = _advise_snapshot()
    if "error" in snap:
        return err(snap["error"])

    if d.get("engine") == "claude":  # cloud: only the exact payload the user previewed and approved
        payload = _claude_payload(d, snap)
        if d.get("digest") != adv.payload_digest(payload):
            return err("What would be sent has changed since you previewed it. Preview it again.", 409)
        if not adv.claude_ready():
            return err("No Anthropic credential found. Set ANTHROPIC_API_KEY in the environment that runs the "
                       "dashboard (or run `ant auth login`), then restart it.", 400)

        def claude_stream():
            try:
                yield from adv.claude_stream(payload)
            except Exception as e:  # noqa: BLE001
                yield f"\n[Claude request failed: {type(e).__name__}. Nothing else was sent.]"

        return StreamingResponse(claude_stream(), media_type="text/plain; charset=utf-8")

    try:
        ollama_url()
    except RuntimeError as e:
        return err(str(e), 400)
    bot = Advisor(snap, str(d.get("model") or DEFAULT_MODEL))
    bot.messages += [m for m in d.get("history", []) if m.get("role") in ("user", "assistant")][-12:]

    def stream():
        try:
            yield from bot.ask(question)
        except Exception as e:  # noqa: BLE001
            yield f"\n[Could not reach Ollama: {type(e).__name__}. Is it running? Try `ollama serve`.]"

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


def _goals_payload() -> dict:
    conn, txns, rules, accts, profile = _ctx()
    mine = goals.list_goals(conn)
    snap = snapshot.build(txns, rules, profile, conn)
    plan = None
    if "error" not in snap:
        plan = goals.build_plan(mine, accts, snap["essential_spending_per_month"], snap["monthly_averages"]["net"])
    return {"goals": mine, "plan": plan, "kinds": [{"value": k, "label": v} for k, v in goals.KINDS.items()]}


async def goals_get(request: Request):
    return ok(_goals_payload())


async def goals_add(request: Request):
    d = await body(request)
    try:
        goals.add_goal(store.connect(), str(d.get("kind", "")), str(d.get("name", "")), d.get("target"), d.get("target_date"),
                       d.get("current"), d.get("months"), d.get("monthly"), int(d.get("priority") or 50))
    except ValueError as e:
        return err(str(e))
    return ok(_goals_payload())


async def goals_delete(request: Request):
    try:
        goals.delete_goal(store.connect(), int(request.query_params.get("id", "")))
    except ValueError:
        return err("id is required")
    return ok(_goals_payload())


def create_app() -> Starlette:
    routes = [
        Route("/api/overview", overview), Route("/api/accounts", list_accounts),
        Route("/api/accounts", save_account, methods=["POST"]),
        Route("/api/transactions", transactions), Route("/api/uncategorized", uncategorized),
        Route("/api/rules", add_rule, methods=["POST"]),
        Route("/api/profile", profile_get), Route("/api/profile", profile_put, methods=["PUT"]),
        Route("/api/import", import_upload, methods=["POST"]),
        Route("/api/report.pdf", report_pdf), Route("/api/ollama", ollama_models),
        Route("/api/advise", advise, methods=["POST"]), Route("/api/advise/preview", advise_preview, methods=["POST"]),
        Route("/api/claude", claude_status),
        Route("/api/goals", goals_get), Route("/api/goals", goals_add, methods=["POST"]),
        Route("/api/goals", goals_delete, methods=["DELETE"]),
        Mount("/", StaticFiles(directory=STATIC, html=True), name="static"),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(Guard)
    return app


def serve(port: int = 8780, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn
    url = f"http://127.0.0.1:{port}"
    print(f"fin-client dashboard on {url}  (this PC only; data in {data_dir()})  Ctrl+C to stop")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
