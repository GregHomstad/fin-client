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

from fin_client import accounts, analytics, goals, labeler, labels, recurring, report, snapshot, store
from fin_client import advisor as adv
from fin_client import ai_categorize as ai
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


def _rec_json(items) -> list[dict]:
    return [{"merchant": r.merchant, "cadence": r.cadence, "amount": float(r.amount), "count": r.count,
             "first": r.first_date.isoformat() if r.first_date else None, "last": r.last_date.isoformat(),
             "next": r.next_expected.isoformat(), "monthly_cost": float(r.monthly_cost), "status": r.status,
             "account": r.account, "follow_up": r.follow_up} for r in items]


async def overview(request: Request):
    conn, txns, rules, accts, profile = _ctx()
    months = analytics.cashflow(txns, rules)
    snap = snapshot.build(txns, rules, profile, conn)
    rec = recurring.analyze(txns, snapshot._since(profile))
    unc = labeler.uncategorized_merchants(txns, rules)
    return ok({
        "accounts": accts, "net_worth": accounts.net_worth(accts),
        "months": [_month(m) for m in months],
        "snapshot": None if "error" in snap else snap,
        "recurring": _rec_json(rec["active"] + rec["yearly"]),
        "recurring_sections": {"active": _rec_json(rec["active"]), "yearly": _rec_json(rec["yearly"]),
                               "stopped": _rec_json(rec["stopped"])},
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
                        d.get("apr_pct"), d.get("min_payment"), None, str(d.get("notes", "")),
                        d.get("earmarked"), str(d.get("earmark_for", "")))
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
    return ok({"ready": adv.claude_ready(), "via": adv.claude_via(), "sdk": importlib.util.find_spec("anthropic") is not None,
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
            return err("Claude is not available here. Log in to Claude Code (run `claude` once in a terminal) or set "
                       "ANTHROPIC_API_KEY, then restart the dashboard.", 400)

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


def _cat_ctx():
    conn, txns, rules, accts, _ = _ctx()
    types = {a["name"]: a["label"].lower() for a in accts}
    return conn, txns, rules, types


def _cands(d: dict):
    conn, txns, rules, types = _cat_ctx()
    cands = ai.candidates(txns, labels.load_all(conn), types, bool(d.get("redo")))
    if d.get("limit"):
        cands = cands[:int(d["limit"])]
    return conn, txns, rules, types, cands


async def cat_status(request: Request):
    conn, txns, rules, types = _cat_ctx()
    lab = labels.load_all(conn)
    keys = {ai.normalize(t.description) for t in txns}
    keys.discard("")
    mine = sum(1 for k in keys if lab.get(k, {}).get("source") == "user")
    auto = sum(1 for k in keys if lab.get(k, {}).get("source") == "ai" and lab[k]["confidence"] >= labels.AI_MIN)
    review = sum(1 for k in keys if lab.get(k, {}).get("source") == "ai" and lab[k]["confidence"] < labels.AI_MIN)
    todo = ai.candidates(txns, lab, types)
    spend = sum(abs(float(t.amount)) for t in txns if t.amount < 0) or 1
    unc = sum(abs(float(t.amount)) for t in txns if t.amount < 0 and categorize(t.description, t.amount, rules, t.bank_category) == "Uncategorized")
    return ok({"merchants": len(keys), "yours": mine, "ai_applied": auto, "needs_review": review, "unlabeled": len(todo),
               "unlabeled_spend": round(sum(c["total"] for c in todo), 2), "uncategorized_pct": round(unc / spend * 100, 1),
               "taxonomy": list(ai.TAXONOMY), "ai_min": labels.AI_MIN})


async def cat_preview(request: Request):
    d = await body(request)
    conn, txns, rules, types, cands = _cands(d)
    out = {"merchants": len(cands), "dollars": round(sum(c["total"] for c in cands), 2), "ready": adv.claude_ready()}
    if d.get("engine") == "claude":
        payloads = ai.claude_payloads(cands, labels.user_examples(conn), d.get("model") or None)
        out |= {"text": ai.render_payloads(payloads), "digest": ai.payloads_digest(payloads), "requests": len(payloads),
                "model": payloads[0]["model"] if payloads else adv.CLAUDE_MODEL}
    return ok(out)


async def cat_run(request: Request):
    """Streams one JSON object per line: progress, then a final result. Claude needs the previewed digest."""
    d = await body(request)
    engine = "claude" if d.get("engine") == "claude" else "local"
    conn, txns, rules, types, cands = _cands(d)
    model = d.get("model") or None
    if engine == "claude":
        payloads = ai.claude_payloads(cands, labels.user_examples(conn), model)
        if d.get("digest") != ai.payloads_digest(payloads):
            return err("What would be sent has changed since you previewed it. Preview it again.", 409)
        if not adv.claude_ready():
            return err("No Anthropic credential found. Set ANTHROPIC_API_KEY and restart the dashboard.", 400)
    else:
        try:
            ollama_url()
        except RuntimeError as e:
            return err(str(e), 400)
    size = 40 if engine == "claude" else 15

    def stream():
        tot = {"merchants": len(cands), "saved": 0, "applied_automatically": 0, "needs_review": 0, "unparsed": 0}
        try:
            run_conn = store.connect()  # SQLite connections are per-thread; this generator runs in a worker thread
            for i in range(0, len(cands), size):
                part = ai.run(run_conn, cands[i:i + size], engine, model, size=size)
                for k in ("saved", "applied_automatically", "needs_review", "unparsed"):
                    tot[k] += part[k]
                yield json.dumps({"done": min(i + size, len(cands)), "total": len(cands)}) + chr(10)
            yield json.dumps({"result": tot}) + chr(10)
        except Exception as e:  # noqa: BLE001
            yield json.dumps({"error": f"{type(e).__name__}: the run stopped. Labels saved so far are kept.", "result": tot}) + chr(10)

    return StreamingResponse(stream(), media_type="application/x-ndjson")


async def labels_review(request: Request):
    conn, txns, rules, types = _cat_ctx()
    lab = labels.load_all(conn)
    stats: dict[str, dict] = {}
    for t in txns:
        k = ai.normalize(t.description)
        if k in lab:
            e = stats.setdefault(k, {"n": 0, "total": 0.0})
            e["n"] += 1
            e["total"] += abs(float(t.amount))
    rows = [{"key": k, "merchant": v["example"] or k, "category": v["category"], "confidence": round(v["confidence"], 2),
             "source": v["source"], "model": v["model"], "n": stats.get(k, {}).get("n", 0),
             "total": round(stats.get(k, {}).get("total", 0), 2)} for k, v in lab.items() if k in stats]
    queue = sorted((r for r in rows if r["source"] == "ai" and r["confidence"] < labels.AI_MIN), key=lambda r: -r["total"])
    applied = sorted((r for r in rows if r["source"] == "ai" and r["confidence"] >= labels.AI_MIN), key=lambda r: -r["total"])[:200]
    mine = sorted((r for r in rows if r["source"] == "user"), key=lambda r: -r["total"])[:200]
    return ok({"review": queue, "applied": applied, "yours": mine, "taxonomy": list(ai.TAXONOMY)})


async def labels_set(request: Request):
    d = await body(request)
    key, cat = str(d.get("key", "")).strip(), ai.canonical(d.get("category", "")) or str(d.get("category", "")).strip()
    if not key or not cat:
        return err("key and category are required")
    labels.set_label(store.connect(), key, cat, "user", 1.0, "", str(d.get("merchant", ""))[:70])
    return ok({"saved": True})


async def labels_delete(request: Request):
    key = request.query_params.get("key", "")
    if not key:
        return err("key is required")
    labels.delete_label(store.connect(), key)
    return ok({"deleted": True})


async def cat_eval(request: Request):
    """Local-only accuracy check against the merchants you labeled (Claude evaluation is CLI-only, with a preview)."""
    d = await body(request)
    conn, txns, rules, types = _cat_ctx()
    try:
        ollama_url()
        return ok(ai.evaluate(conn, txns, rules, types, "local", d.get("model") or None))
    except RuntimeError as e:
        return err(str(e))
    except Exception as e:  # noqa: BLE001
        return err(f"Evaluation failed: {type(e).__name__}. Is Ollama running?", 502)


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
        Route("/api/categorize/status", cat_status), Route("/api/categorize/preview", cat_preview, methods=["POST"]),
        Route("/api/categorize/run", cat_run, methods=["POST"]), Route("/api/categorize/eval", cat_eval, methods=["POST"]),
        Route("/api/labels/review", labels_review), Route("/api/labels", labels_set, methods=["POST"]),
        Route("/api/labels", labels_delete, methods=["DELETE"]),
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
