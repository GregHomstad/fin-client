import argparse
import json
import os
import sys
from pathlib import Path

from fin_client import analytics, recurring, snapshot, store, tax
from fin_client.advisor import DEFAULT_MODEL, Advisor, ollama_url
from fin_client.categorize import categorize, load_rules
from fin_client.importers import import_file
from fin_client.paths import data_dir


def categorize_cmd(args, conn, ask=input, say=print) -> int:
    """AI-categorize merchants. Claude sends merchant text only, after you review the exact requests."""
    from fin_client import accounts, ai_categorize as ai, labels
    txns = store.all_transactions(conn)
    rules = load_rules()
    types = {a["name"]: a["label"].lower() for a in accounts.list_accounts(conn)}
    if args.eval:
        if args.engine == "claude":
            from fin_client import advisor as adv
            if not adv.claude_ready():
                print("error: no Anthropic credential found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
                return 1
            prev = ai.evaluate(conn, txns, rules, types, "claude", args.model, preview_only=True)
            if "error" in prev:
                say(prev["error"])
                return 1
            say("\n--- EXACTLY WHAT WILL BE SENT TO ANTHROPIC (merchants you labeled, plus your labels as examples) ---")
            say(ai.render_payloads(prev["payloads"]))
            say("--- END ---")
            if ask("Send to Claude? [y/N] ").strip().lower() not in ("y", "yes"):
                say("Not sent.")
                return 0
        res = ai.evaluate(conn, txns, rules, types, args.engine, args.model)
        if "error" in res:
            say(res["error"])
            return 1
        say(f"{res['engine']} / {res['model']} on {res['n']} merchants you labeled:")
        say(f"  AI accuracy            {res['ai_accuracy']}%")
        say(f"  rules + bank labels    {res['rules_only_accuracy']}%")
        if res["confident_accuracy"] is not None:
            say(f"  AI when confident      {res['confident_accuracy']}% ({res['confident_n']} merchants)")
        for m in res["mistakes"][:10]:
            say(f"  miss: {m['merchant']:<32} you said {m['expected']:<18} AI said {m['ai']} ({m['confidence']:.2f})")
        return 0
    cands = ai.candidates(txns, labels.load_all(conn), types, args.redo)
    if args.limit:
        cands = cands[:args.limit]
    if not cands:
        say("Every merchant already has a label.")
        return 0
    say(f"{len(cands)} merchants to categorize ({sum(c['total'] for c in cands):,.0f} dollars of activity).")
    examples = labels.user_examples(conn)
    if args.engine == "claude":
        from fin_client import advisor as adv
        if not adv.claude_ready():
            print("error: no Anthropic credential found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        payloads = ai.claude_payloads(cands, examples, args.model)
        say("\n--- EXACTLY WHAT WILL BE SENT TO ANTHROPIC (merchant text, no account numbers, no raw transactions) ---")
        say(ai.render_payloads(payloads))
        say(f"--- END: {len(payloads)} request(s), fingerprint {ai.payloads_digest(payloads)} ---")
        if ask("Send to Claude? [y/N] ").strip().lower() not in ("y", "yes"):
            say("Not sent.")
            return 0
    res = ai.run(conn, cands, args.engine, args.model, examples, lambda d, t: say(f"  {d}/{t} merchants"))
    say(f"Done: {res['saved']} labels saved, {res['applied_automatically']} applied automatically (confidence >= {labels.AI_MIN}), "
        f"{res['needs_review']} waiting for your review, {res['unparsed']} the model skipped.")
    say("Review and correct them in the dashboard (Transactions, AI review). Your corrections teach the next run.")
    return 0


def fullplan_cmd(args, conn, ask=input, say=print) -> int:
    """Build Victor's full plan as a PDF. The narrative is written locally (Ollama) unless you approve Claude."""
    from datetime import date

    from fin_client import advisor as adv
    from fin_client import report
    from fin_client.persona import PLAN_REQUEST
    snap = snapshot.build(store.all_transactions(conn), load_rules(), snapshot.profile_for(conn), conn)
    if "error" in snap:
        print(snap["error"], file=sys.stderr)
        return 1
    narrative, label = None, ""
    if args.narrative == "local":
        try:
            adv.ollama_url()
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        model = args.model or adv.DEFAULT_MODEL
        say(f"Writing the plan with {model} on this PC (a minute or two)...")
        try:
            narrative = "".join(adv.Advisor(snap, model).ask(PLAN_REQUEST))
        except Exception as e:  # noqa: BLE001
            print(f"error: could not reach Ollama ({type(e).__name__}). Start it with: ollama serve", file=sys.stderr)
            return 1
        label = f"{model}, local"
    elif args.narrative == "claude":
        if not adv.claude_ready():
            print("error: no Anthropic credential found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        payload = adv.build_claude_payload(snap, [], PLAN_REQUEST, args.include_merchants, args.model)
        say("\n--- EXACTLY WHAT WILL BE SENT TO ANTHROPIC ---")
        say(adv.render_payload(payload))
        say("--- END ---")
        if ask(f"Send to Claude ({payload['model']})? [y/N] ").strip().lower() not in ("y", "yes"):
            say("Not sent. Re-run with --narrative local or none.")
            return 0
        say("Writing the plan with Claude...")
        try:
            narrative = "".join(adv.claude_stream(payload))
        except Exception as e:  # noqa: BLE001
            print(f"error: Claude request failed ({type(e).__name__}).", file=sys.stderr)
            return 1
        label = f"{payload['model']}, cloud, approved by you"
    out = args.out or data_dir() / "reports" / f"fin-client-full-plan-{date.today():%Y-%m-%d}.pdf"
    try:
        path = report.build_pdf(snap, out, narrative, label)
    except (RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if narrative:
        path.with_suffix(".md").write_text(narrative, encoding="utf-8")
    say(f"Wrote {path}")
    return 0


def claude_chat(snap: dict, include_merchants: bool, ask=input, say=print) -> int:
    """Chat with Claude. Every send is previewed in full and needs a typed 'y'."""
    from fin_client import advisor as adv
    if not adv.claude_ready():
        print("error: no Anthropic credential found. Set ANTHROPIC_API_KEY (or run `ant auth login`).", file=sys.stderr)
        return 1
    say("Claude (cloud) mode. Nothing is sent until you approve it. Educational only, not licensed advice.")
    history: list[dict] = []
    shown_full = False
    while True:
        try:
            q = ask("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            say("")
            return 0
        if q.lower() in ("exit", "quit"):
            return 0
        if not q:
            continue
        payload = adv.build_claude_payload(snap, history, q, include_merchants)
        digest = adv.payload_digest(payload)
        if not shown_full:
            say("\n--- EXACTLY WHAT WILL BE SENT TO ANTHROPIC ---")
            say(adv.render_payload(payload))
            say("--- END ---")
            shown_full = True
        else:
            say(f"\nWill send the same numbers (fingerprint {digest}), the conversation so far, and your new message.")
            if ask("Type v to view everything, or press Enter to continue: ").strip().lower() == "v":
                say(adv.render_payload(payload))
        if ask(f"Send to Claude ({payload['model']})? [y/N] ").strip().lower() not in ("y", "yes"):
            say("Not sent.")
            continue
        parts: list[str] = []
        try:
            for piece in adv.claude_stream(payload):
                parts.append(piece)
                print(piece, end="", flush=True)
            print()
        except Exception as e:  # noqa: BLE001
            say(f"\nerror: Claude request failed ({type(e).__name__}).")
            continue
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": "".join(parts)}]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fin-client", description=__doc__)
    p.add_argument("--data", type=Path, help="Use this private data directory instead of the default")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="Import a CSV/OFX/QFX bank export into the local store")
    imp.add_argument("file", type=Path)
    imp.add_argument("--account", default="default")
    sub.add_parser("summary", help="Monthly income/spending summary")
    sub.add_parser("where", help="Show the private data directory")
    sub.add_parser("categories", help="Spending by category per month, plus uncategorized merchants")
    sub.add_parser("recurring", help="Detected recurring charges and deposits")
    sub.add_parser("cashflow", help="Monthly cash flow, savings rate and averages")
    sub.add_parser("plan", help="Debt payoff, emergency fund and retirement calculations (no model)")
    tx = sub.add_parser("tax", help="Federal income tax estimate and 401(k)/IRA what-ifs from profile.json (no data needed)")
    tx.add_argument("--year", type=int, default=2026)
    db = sub.add_parser("dashboard", help="Start the local web dashboard (127.0.0.1 only)")
    db.add_argument("--port", type=int, default=8780)
    db.add_argument("--no-browser", action="store_true")
    cat = sub.add_parser("categorize", help="Categorize every merchant with AI (local Ollama by default)")
    cat.add_argument("--engine", choices=["local", "claude"], default="local")
    cat.add_argument("--model", help="Model name (default: llama3:8b locally, claude-opus-5-5 for Claude)")
    cat.add_argument("--limit", type=int, help="Only the N biggest merchants")
    cat.add_argument("--redo", action="store_true", help="Re-label merchants the AI already labeled (yours are never touched)")
    cat.add_argument("--eval", action="store_true", help="Measure accuracy against the merchants you labeled")
    fp = sub.add_parser("fullplan", help="Victor's full wealth plan as a PDF (local narrative by default)")
    fp.add_argument("--narrative", choices=["local", "claude", "none"], default="local")
    fp.add_argument("--model", help="Model name for the narrative")
    fp.add_argument("--include-merchants", action="store_true", help="With claude: also send recurring merchant names")
    fp.add_argument("--out", type=Path, help="Output PDF path (must be outside the repo)")
    sub.add_parser("label", help="Interactively categorize uncategorized merchants (saves rules.json)")
    rp = sub.add_parser("report", help="Export a PDF report (aggregates only) to your private data dir")
    rp.add_argument("--out", type=Path, help="Output path (must be outside the repo)")
    adv = sub.add_parser("advise", help="Chat with the local Ollama advisor about your finances")
    adv.add_argument("--model", default=os.environ.get("FIN_CLIENT_MODEL", DEFAULT_MODEL))
    adv.add_argument("--claude", action="store_true",
                     help="Use Claude (cloud) instead of Ollama. You review and approve every send.")
    adv.add_argument("--include-merchants", action="store_true",
                     help="With --claude: also send recurring merchant names (hidden by default)")
    adv.add_argument("--show-context", action="store_true",
                     help="Print exactly what the model would be given, then exit (no model call)")
    args = p.parse_args(argv)
    if args.data:
        os.environ["FIN_CLIENT_DATA"] = str(args.data)
    if args.cmd == "dashboard":
        from fin_client import web
        web.serve(args.port, not args.no_browser)
        return 0

    if args.cmd == "where":
        print(data_dir())
        return 0
    if args.cmd == "tax":
        res = tax.estimate(snapshot.load_profile(), args.year)
        print(json.dumps(res, indent=1))
        return 1 if "error" in res else 0
    conn = store.connect()
    if args.cmd == "import":
        try:
            txns = import_file(args.file, args.account)
        except (ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        added = store.add(conn, txns)
        print(f"Parsed {len(txns)} transactions, added {added} new.")
    elif args.cmd == "fullplan":
        return fullplan_cmd(args, conn)
    elif args.cmd == "categorize":
        return categorize_cmd(args, conn)
    elif args.cmd == "label":
        from fin_client import labeler
        try:
            labeler.run(store.all_transactions(conn))
        except (EOFError, KeyboardInterrupt):
            print("\nStopped; rules saved so far are kept.")
    elif args.cmd == "report":
        from datetime import date

        from fin_client import report
        snap = snapshot.build(store.all_transactions(conn), load_rules(), snapshot.profile_for(conn), conn)
        if "error" in snap:
            print(snap["error"], file=sys.stderr)
            return 1
        out = args.out or data_dir() / "reports" / f"fin-client-report-{date.today():%Y-%m-%d}.pdf"
        try:
            print(f"Wrote {report.build_pdf(snap, out)}")
        except (RuntimeError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    elif args.cmd == "plan":
        snap = snapshot.build(store.all_transactions(conn), load_rules(), snapshot.profile_for(conn), conn)
        if "error" in snap:
            print(snap["error"], file=sys.stderr)
            return 1
        print(json.dumps(snap["planning"], indent=1))
    elif args.cmd == "advise":
        snap = snapshot.build(store.all_transactions(conn), load_rules(), snapshot.profile_for(conn), conn)
        if args.show_context:
            print(json.dumps(snap, indent=1))
            return 0
        if "error" in snap:
            print(snap["error"], file=sys.stderr)
            return 1
        if args.claude:
            return claude_chat(snap, args.include_merchants)
        try:
            ollama_url()
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        bot = Advisor(snap, args.model)
        print(f"Local advisor ({args.model}). Educational only, not licensed advice. Ctrl+C or 'exit' to quit.")
        while True:
            try:
                q = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if q.lower() in ("exit", "quit"):
                return 0
            if not q:
                continue
            try:
                for piece in bot.ask(q):
                    print(piece, end="", flush=True)
                print()
            except (OSError, RuntimeError) as e:
                print(f"\nerror talking to Ollama ({e}). Is it running? Try: ollama serve", file=sys.stderr)
                return 1
    elif args.cmd in ("categories", "recurring", "cashflow"):
        txns = store.all_transactions(conn)
        rules = load_rules()
        if args.cmd == "categories":
            for m in analytics.cashflow(txns, rules):
                print(m.month)
                for cat, amt in sorted(m.by_category.items(), key=lambda kv: -kv[1]):
                    print(f"  {cat:<16}{amt:>10.2f}")
            unk = sorted({t.description for t in txns
                          if t.amount < 0 and categorize(t.description, t.amount, rules, t.bank_category) == "Uncategorized"})
            if unk:
                print("\nUncategorized (add keywords to rules.json in your data dir):")
                for d in unk:
                    print(f"  {d}")
        elif args.cmd == "recurring":
            items = recurring.detect(txns)
            for r in items:
                print(f"{r.merchant:<28}{r.cadence:<10}{r.amount:>10}  x{r.count}  "
                      f"next ~{r.next_expected}  ({r.monthly_cost}/mo)")
            if not items:
                print("No recurring items found (need 3+ similar occurrences).")
        else:
            months = analytics.cashflow(txns, rules)
            for m in months:
                rate = f"{m.savings_rate}%" if m.savings_rate is not None else "n/a"
                print(f"{m.month}  income {m.income:>10.2f}  spending {m.spending:>10.2f}  "
                      f"net {m.net:>10.2f}  saved {rate}")
            for k, v in analytics.averages(months).items():
                print(f"{k}: {v}")
    elif args.cmd == "summary":
        for month, v in store.monthly_summary(conn).items():
            net = v["income"] + v["spending"]
            print(f"{month}  income {v['income']:>10}  spending {v['spending']:>10}  net {net:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
