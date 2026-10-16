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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fin-client", description=__doc__)
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
    adv = sub.add_parser("advise", help="Chat with the local Ollama advisor about your finances")
    adv.add_argument("--model", default=os.environ.get("FIN_CLIENT_MODEL", DEFAULT_MODEL))
    adv.add_argument("--show-context", action="store_true",
                     help="Print exactly what the model would be given, then exit (no model call)")
    args = p.parse_args(argv)

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
    elif args.cmd == "plan":
        snap = snapshot.build(store.all_transactions(conn), load_rules(), snapshot.load_profile())
        if "error" in snap:
            print(snap["error"], file=sys.stderr)
            return 1
        print(json.dumps(snap["planning"], indent=1))
    elif args.cmd == "advise":
        snap = snapshot.build(store.all_transactions(conn), load_rules(), snapshot.load_profile())
        if args.show_context:
            print(json.dumps(snap, indent=1))
            return 0
        if "error" in snap:
            print(snap["error"], file=sys.stderr)
            return 1
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
                          if t.amount < 0 and categorize(t.description, t.amount, rules) == "Uncategorized"})
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
