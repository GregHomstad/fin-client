"""Local SQLite store, kept in the private data dir (never in the repo)."""
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fin_client.models import Transaction
from fin_client.paths import data_dir


def connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or data_dir() / "ledger.db")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transactions (
            date TEXT NOT NULL, description TEXT NOT NULL,
            amount TEXT NOT NULL, account TEXT NOT NULL,
            UNIQUE(date, description, amount, account))"""
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "bank_category" not in cols:  # migrate older databases
        conn.execute("ALTER TABLE transactions ADD COLUMN bank_category TEXT NOT NULL DEFAULT ''")
    return conn


def add(conn: sqlite3.Connection, txns: list[Transaction]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO transactions (date, description, amount, account, bank_category) VALUES (?,?,?,?,?)",
        [(t.date.isoformat(), t.description, str(t.amount), t.account, t.bank_category) for t in txns],
    )
    # re-importing fills in bank categories for rows stored before that column existed
    conn.executemany(
        "UPDATE transactions SET bank_category=? WHERE date=? AND description=? AND amount=? AND account=? "
        "AND bank_category=''",
        [(t.bank_category, t.date.isoformat(), t.description, str(t.amount), t.account)
         for t in txns if t.bank_category],
    )
    conn.commit()
    return conn.total_changes - before


def all_transactions(conn: sqlite3.Connection) -> list[Transaction]:
    return [
        Transaction(date.fromisoformat(d), desc, Decimal(a), acct, bc)
        for d, desc, a, acct, bc in conn.execute(
            "SELECT date, description, amount, account, bank_category FROM transactions ORDER BY date"
        )
    ]


def monthly_summary(conn: sqlite3.Connection) -> dict[str, dict[str, Decimal]]:
    out: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"income": Decimal(0), "spending": Decimal(0)}
    )
    for d, amt in conn.execute("SELECT date, amount FROM transactions"):
        a = Decimal(amt)
        out[d[:7]]["income" if a > 0 else "spending"] += a
    return dict(sorted(out.items()))
