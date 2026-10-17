"""Accounts: one place for checking, savings, credit cards, loans and investments.

Transactions come from imports; balances are typed in (bank CSVs rarely carry them). Net worth is
assets minus liabilities. Liability balances are stored as positive amounts owed.
"""
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation

ASSET_TYPES = {"checking", "savings", "cash", "investment", "retirement", "hsa", "property"}
LIABILITY_TYPES = {"credit_card", "loan", "mortgage", "auto_loan", "student_loan"}
ALL_TYPES = sorted(ASSET_TYPES | LIABILITY_TYPES)
LABELS = {"checking": "Checking", "savings": "Savings", "cash": "Cash", "investment": "Investments",
          "retirement": "Retirement (401k/IRA)", "hsa": "HSA", "property": "Property",
          "credit_card": "Credit card", "loan": "Loan", "mortgage": "Mortgage", "auto_loan": "Auto loan",
          "student_loan": "Student loan"}


def init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS accounts (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, balance TEXT, apr_pct TEXT,
            min_payment TEXT, as_of TEXT, notes TEXT NOT NULL DEFAULT '')"""
    )


def guess_type(name: str) -> str:
    n = name.lower()
    for words, t in ((("credit", "card", "visa", "mastercard", "amex", "discover"), "credit_card"),
                     (("mortgage",), "mortgage"), (("auto loan", "car loan"), "auto_loan"),
                     (("student",), "student_loan"), (("loan",), "loan"),
                     (("401", " ira", "roth", "retire"), "retirement"), (("hsa",), "hsa"),
                     (("brokerage", "invest"), "investment"), (("saving", "money market"), "savings")):
        if any(w in n for w in words):
            return t
    return "checking"


def ensure_accounts(conn: sqlite3.Connection) -> None:
    """Create an account row for every account label seen in transactions."""
    init(conn)
    for (name,) in conn.execute(
        "SELECT DISTINCT account FROM transactions WHERE account NOT IN (SELECT name FROM accounts)"
    ).fetchall():
        conn.execute("INSERT INTO accounts (name, type) VALUES (?, ?)", (name, guess_type(name)))
    conn.commit()


def _num(x) -> Decimal | None:
    if x in (None, ""):
        return None
    try:
        return Decimal(str(x).replace("$", "").replace(",", "").strip())
    except InvalidOperation as e:
        raise ValueError(f"Not a number: {x!r}") from e


def upsert(conn: sqlite3.Connection, name: str, type_: str, balance=None, apr_pct=None, min_payment=None,
           as_of: str | None = None, notes: str = "") -> None:
    init(conn)
    name = name.strip()
    if not name:
        raise ValueError("Account name is required")
    if type_ not in ALL_TYPES:
        raise ValueError(f"type must be one of {ALL_TYPES}")
    bal, apr, mn = _num(balance), _num(apr_pct), _num(min_payment)
    if type_ in LIABILITY_TYPES and bal is not None:
        bal = abs(bal)  # always store the amount owed as a positive number
    if bal is not None and as_of is None:
        as_of = date.today().isoformat()
    conn.execute(
        """INSERT INTO accounts (name, type, balance, apr_pct, min_payment, as_of, notes)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET type=excluded.type, balance=excluded.balance,
             apr_pct=excluded.apr_pct, min_payment=excluded.min_payment, as_of=excluded.as_of,
             notes=excluded.notes""",
        (name, type_, None if bal is None else str(bal), None if apr is None else str(apr),
         None if mn is None else str(mn), as_of, notes),
    )
    conn.commit()


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    ensure_accounts(conn)
    stats = {r[0]: r[1:] for r in conn.execute(
        "SELECT account, COUNT(*), MIN(date), MAX(date) FROM transactions GROUP BY account")}
    out = []
    for name, type_, bal, apr, mn, as_of, notes in conn.execute(
        "SELECT name, type, balance, apr_pct, min_payment, as_of, notes FROM accounts ORDER BY type, name"
    ):
        n, first, last = stats.get(name, (0, None, None))
        out.append({"name": name, "type": type_, "label": LABELS.get(type_, type_),
                    "kind": "liability" if type_ in LIABILITY_TYPES else "asset",
                    "balance": None if bal is None else float(bal),
                    "apr_pct": None if apr is None else float(apr),
                    "min_payment": None if mn is None else float(mn),
                    "as_of": as_of, "notes": notes, "transactions": n, "first_date": first, "last_date": last})
    return out


def net_worth(accts: list[dict]) -> dict:
    assets = sum(a["balance"] for a in accts if a["kind"] == "asset" and a["balance"] is not None)
    liabilities = sum(a["balance"] for a in accts if a["kind"] == "liability" and a["balance"] is not None)
    by_type: dict[str, float] = {}
    for a in accts:
        if a["balance"] is not None:
            by_type[a["type"]] = by_type.get(a["type"], 0) + a["balance"]
    return {"assets": round(assets, 2), "liabilities": round(liabilities, 2), "net_worth": round(assets - liabilities, 2),
            "by_type": {k: round(v, 2) for k, v in by_type.items()},
            "missing_balances": [a["name"] for a in accts if a["balance"] is None]}


def effective_profile(profile: dict, accts: list[dict]) -> dict:
    """profile.json plus what the accounts already tell us. Explicit profile values always win."""
    p = dict(profile)
    known = [a for a in accts if a["balance"] is not None]

    debts = {d["name"]: d for d in p.get("debts", [])}
    for a in known:
        if a["kind"] == "liability" and a["balance"] > 0 and a["apr_pct"] is not None:
            d = {"name": a["name"], "balance": a["balance"], "apr_pct": a["apr_pct"]}
            if a["min_payment"] is not None:
                d["min_payment"] = a["min_payment"]
            debts[a["name"]] = d
    if debts:
        p["debts"] = list(debts.values())

    cash = [a["balance"] for a in known if a["type"] in ("checking", "savings", "cash")]
    if "cash_savings" not in p and cash:
        p["cash_savings"] = round(sum(cash), 2)
    retire = [a["balance"] for a in known if a["type"] in ("retirement", "investment")]
    if "retirement_balance" not in p and retire:
        p["retirement_balance"] = round(sum(retire), 2)
    return p
