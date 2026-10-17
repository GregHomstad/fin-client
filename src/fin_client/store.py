"""Local SQLite store, kept in the private data dir (never in the repo)."""
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fin_client import transfers
from fin_client.models import Transaction
from fin_client.paths import data_dir


SCHEMA = """CREATE TABLE IF NOT EXISTS transactions (
    date TEXT NOT NULL, description TEXT NOT NULL, amount TEXT NOT NULL, account TEXT NOT NULL,
    bank_category TEXT NOT NULL DEFAULT '', occurrence INTEGER NOT NULL DEFAULT 1,
    UNIQUE(date, description, amount, account, occurrence))"""


def connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or data_dir() / "ledger.db")
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='transactions'").fetchone()
    if not exists:
        conn.execute(SCHEMA)
        conn.execute("PRAGMA user_version = 2")
    elif conn.execute("PRAGMA user_version").fetchone()[0] < 2:
        # v2: genuine identical same-day transactions (two $9.86 fuel purchases) must both survive an import
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
        bank = "bank_category" if "bank_category" in cols else "''"
        conn.execute("ALTER TABLE transactions RENAME TO transactions_v1")
        conn.execute(SCHEMA)
        conn.execute(f"INSERT INTO transactions (date, description, amount, account, bank_category, occurrence) "
                     f"SELECT date, description, amount, account, {bank}, 1 FROM transactions_v1")
        conn.execute("DROP TABLE transactions_v1")
        conn.execute("PRAGMA user_version = 2")
    conn.commit()
    return conn


def add(conn: sqlite3.Connection, txns: list[Transaction]) -> int:
    """Insert transactions; re-importing the same file adds nothing, but identical rows within a file all count."""
    before = conn.total_changes
    seen: dict[tuple, int] = {}
    rows = []
    for t in txns:
        key = (t.date.isoformat(), t.description, str(t.amount), t.account)
        seen[key] = seen.get(key, 0) + 1
        rows.append(key + (t.bank_category, seen[key]))
    conn.executemany(
        "INSERT OR IGNORE INTO transactions (date, description, amount, account, bank_category, occurrence) VALUES (?,?,?,?,?,?)", rows)
    # re-importing fills in bank categories for rows stored before that column existed
    conn.executemany(
        "UPDATE transactions SET bank_category=? WHERE date=? AND description=? AND amount=? AND account=? AND bank_category=''",
        [(t.bank_category, t.date.isoformat(), t.description, str(t.amount), t.account) for t in txns if t.bank_category])
    conn.commit()
    return conn.total_changes - before


def all_transactions(conn: sqlite3.Connection, match_transfers: bool = True) -> list[Transaction]:
    txns = [
        Transaction(date.fromisoformat(d), desc, Decimal(a), acct, bc)
        for d, desc, a, acct, bc in conn.execute(
            "SELECT date, description, amount, account, bank_category FROM transactions ORDER BY date"
        )
    ]
    return transfers.mark_internal(txns) if match_transfers else txns


def monthly_summary(conn: sqlite3.Connection) -> dict[str, dict[str, Decimal]]:
    out: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"income": Decimal(0), "spending": Decimal(0)}
    )
    for d, amt in conn.execute("SELECT date, amount FROM transactions"):
        a = Decimal(amt)
        out[d[:7]]["income" if a > 0 else "spending"] += a
    return dict(sorted(out.items()))
