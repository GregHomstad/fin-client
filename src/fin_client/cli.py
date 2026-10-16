import argparse
import sys
from pathlib import Path

from fin_client import store
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
    args = p.parse_args(argv)

    if args.cmd == "where":
        print(data_dir())
        return 0
    conn = store.connect()
    if args.cmd == "import":
        try:
            txns = import_file(args.file, args.account)
        except (ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        added = store.add(conn, txns)
        print(f"Parsed {len(txns)} transactions, added {added} new.")
    elif args.cmd == "summary":
        for month, v in store.monthly_summary(conn).items():
            net = v["income"] + v["spending"]
            print(f"{month}  income {v['income']:>10}  spending {v['spending']:>10}  net {net:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
