"""Generic bank CSV importer with header auto-detection."""
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fin_client.models import Transaction

DATE_COLS = ("date", "posted date", "posting date", "transaction date")
DESC_COLS = ("description", "memo", "payee", "name", "details")
AMOUNT_COLS = ("amount",)
DEBIT_COLS = ("debit", "withdrawal", "withdrawals")
CREDIT_COLS = ("credit", "deposit", "deposits")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y")


def _pick(headers: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        if n in headers:
            return headers[n]
    return None


def _money(raw: str) -> Decimal:
    s = raw.strip().replace("$", "").replace(",", "")
    if not s:
        return Decimal(0)
    neg = s.startswith("(") and s.endswith(")")
    try:
        val = Decimal(s.strip("()"))
    except InvalidOperation as e:
        raise ValueError(f"Unparseable amount: {raw!r}") from e
    return -val if neg else val


def _date(raw: str) -> date:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unparseable date: {raw!r}")


def _find_header(rows: list[list[str]]) -> int:
    """Index of the header row; some banks put account info lines above it."""
    for i, row in enumerate(rows[:25]):
        cells = {c.strip().lower() for c in row}
        if cells & set(DATE_COLS) and cells & set(DESC_COLS):
            return i
    raise ValueError("Could not find a header row with date and description columns")


def parse_csv(path: Path, account: str = "default") -> list[Transaction]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    start = _find_header(rows)
    names = [h.strip() for h in rows[start]]
    headers = {h.lower(): h for h in names}
    c_date = _pick(headers, DATE_COLS)
    c_desc = _pick(headers, DESC_COLS)
    c_amt = _pick(headers, AMOUNT_COLS)
    c_deb = _pick(headers, DEBIT_COLS)
    c_cred = _pick(headers, CREDIT_COLS)
    c_acct = headers.get("account")
    c_bcat = headers.get("category")
    if not (c_amt or c_deb or c_cred):
        raise ValueError(f"Could not detect an amount column in {list(headers)}")
    out = []
    for cells in rows[start + 1:]:
        row = dict(zip(names, cells))
        if not (row.get(c_date) or "").strip():
            continue
        if c_amt:
            amount = _money(row[c_amt])
        else:
            amount = _money(row.get(c_cred) or "") - abs(_money(row.get(c_deb) or ""))
        label = account
        if account == "default" and c_acct and (row.get(c_acct) or "").strip():
            label = row[c_acct].strip()
        bank_cat = (row.get(c_bcat) or "").strip() if c_bcat else ""
        out.append(Transaction(_date(row[c_date]), row[c_desc].strip(), amount, label, bank_cat))
    return out
