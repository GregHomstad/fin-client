"""Import reconciled bank-statement PDFs into the ledger.

Only statements whose balances reconcile to the penny are imported. Everything else is listed in the report so
you can fix or re-export it; nothing is guessed.
"""
import glob
from collections import Counter
from pathlib import Path

from fin_client import accounts, store
from fin_client.importers import pdf_bank


def expand(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            out += sorted(pp.glob("*.pdf")) + sorted(pp.glob("*.PDF"))
        elif any(ch in p for ch in "*?["):
            out += [Path(x) for x in sorted(glob.glob(p))]
        else:
            out.append(pp)
    return sorted(set(out))


def import_pdfs(conn, paths: list[Path], name_map: dict[str, str] | None = None, dry_run: bool = False) -> dict:
    name_map = name_map or {}
    statements, unreadable, no_statement = [], 0, 0
    for path in paths:
        try:
            found = pdf_bank.parse_pdf(path)
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        if not found:
            no_statement += 1
        statements += found
    chosen = pdf_bank.select_supersets(statements)
    by_acct: dict[str, list] = {}
    for st in chosen:
        by_acct.setdefault(st.last4, []).append(st)

    report = {"pdfs": len(paths), "unreadable": unreadable, "not_bank_statements": no_statement, "accounts": [],
              "skipped": [], "added": 0}
    for last4, sts in sorted(by_acct.items()):
        name = name_map.get(last4) or f"Account ...{last4}"
        good = [s for s in sts if s.reconciled]
        for s in sts:
            if not s.reconciled:
                report["skipped"].append({"account": name, "period": f"{s.start}..{s.end}", "problem": s.problem})
        txns = []
        for s in good:
            txns += [t.__class__(t.date, t.description, t.amount, name, t.bank_category) for t in s.txns]
        existing = Counter((d, a) for d, a in conn.execute(
            "SELECT date, amount FROM transactions WHERE account=?", (name,)))
        # when this account already has rows (for example from a CSV), only add what is not already there
        fresh, used = [], Counter()
        for t in txns:
            k = (t.date.isoformat(), str(t.amount))
            used[k] += 1
            if used[k] > existing.get(k, 0):
                fresh.append(t)
        existed = name in {a["name"] for a in accounts.list_accounts(conn)}
        added = len(fresh) if dry_run else store.add(conn, fresh)
        if not dry_run:
            accounts.ensure_accounts(conn)
            if not existed:  # a busy account is checking; a quiet one (a few lines a month) is savings
                accounts.upsert(conn, name, "checking" if len(txns) / max(len(good), 1) > 40 else "savings")
        report["added"] += added
        report["accounts"].append({"account": name, "last4": last4, "statements": len(sts), "reconciled": len(good),
                                   "first": min((s.start for s in good), default=None), "last": max((s.end for s in good), default=None),
                                   "transactions": len(txns), "added": added})
    return report
