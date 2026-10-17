"""Bank statement PDFs in the common core-banking layout (used by Arvest and Simmons statements).

Each statement has a header ("Account Number XXXX1234  Statement Dates 9/01/26 thru 9/30/26"), a summary (Previous
Balance, Deposits/Credits, Checks/Debits, Current Balance) and a transaction list: "M/DD <detail> <amount> <balance>",
where a debit amount ends in "-" and the merchant text continues on the indented lines below.

Nothing is imported unless a statement reconciles: previous balance + the signed transactions must equal the current
balance. Statements that do not reconcile are reported and skipped, never guessed at.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from fin_client.models import Transaction

AMT = r"[\d,]*\.\d{2}-?"
HEADER = re.compile(r"Account Number\s+X+(\d{4})\s+Statement Dates\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+thru\s+(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
PREV = re.compile(r"Previous Balance\s+(" + AMT + r")", re.I)
SERVICE = re.compile(r"Service Charge\s+(" + AMT + r")", re.I)
INTEREST = re.compile(r"Interest Paid\s+(" + AMT + r")", re.I)
CURR = re.compile(r"(?:Current|Ending) Balance\s+(" + AMT + r")", re.I)
TX = re.compile(r"^\s*(\d{1,2})/(\d{2})\s+(.*?)\s+(" + AMT + r")(?:\s+(" + AMT + r"))?\s*$")
TRAILER = re.compile(r"(?:\s+\d{2}/\d{2}/\d{2})?(?:\s+\d{8}){0,2}\s*$")
STOP = re.compile(r"(?i)^\s*(daily balance|overdraft|interest rate|annual percentage|page\s+\d|account number|statement dates|"
                  r"date\s+description|date\s+\d{1,2}/\d{1,2}/\d{2,4}|primary account|round up|total for|summary|member fdic|\(\d\))")


def money(s: str) -> Decimal:
    neg = s.endswith("-")
    v = Decimal(s.rstrip("-").replace(",", "") or "0")
    return -v if neg else v


def _d(s: str) -> date:
    m, d, y = s.split("/")
    y = int(y)
    return date(2000 + y if y < 100 else y, int(m), int(d))


@dataclass
class Statement:
    last4: str
    start: date
    end: date
    previous: Decimal | None = None
    current: Decimal | None = None
    txns: list[Transaction] = field(default_factory=list)
    balances: list[Decimal | None] = field(default_factory=list)
    service_charge: Decimal | None = None
    interest_paid: Decimal | None = None
    reconciled: bool = False
    problem: str = ""

    @property
    def total(self) -> Decimal:
        return sum((t.amount for t in self.txns), Decimal(0))


def _year_for(month: int, start: date, end: date) -> int:
    if start.year == end.year or month <= end.month:
        return end.year
    return start.year


def parse_text(lines: list[str]) -> list[Statement]:
    """Parse the layout-mode text lines of one PDF into statements (one per account section)."""
    out: list[Statement] = []
    cur: Statement | None = None
    open_tx: tuple[Transaction, list[str]] | None = None

    def close():
        nonlocal open_tx
        if open_tx and cur is not None:
            t, extra = open_tx
            full = " ".join(x for x in extra + [t.description] if x)
            cur.txns[-1] = Transaction(t.date, " ".join(full.split()), t.amount, t.account, t.bank_category)
        open_tx = None

    for line in lines:
        h = HEADER.search(line)
        if h:
            close()
            key = (h.group(1), _d(h.group(2)), _d(h.group(3)))
            if cur is None or (cur.last4, cur.start, cur.end) != key:
                cur = Statement(*key)
                out.append(cur)
            continue
        if cur is None:
            continue
        p, c = PREV.search(line), CURR.search(line)
        if p and cur.previous is None:
            cur.previous = money(p.group(1))
        if c and not TX.match(line):
            cur.current = money(c.group(1))
        sc, ip = SERVICE.search(line), INTEREST.search(line)
        if sc and cur.service_charge is None and not TX.match(line):
            cur.service_charge = money(sc.group(1))
        if ip and cur.interest_paid is None and not TX.match(line):
            cur.interest_paid = money(ip.group(1))
        m = TX.match(line)
        if m:
            desc = TRAILER.sub("", m.group(3)).strip()
            if re.search(r"[A-Za-z]", desc) and m.group(5):  # real lines carry a running balance; breakdown/memo lines do not
                close()
                year = _year_for(int(m.group(1)), cur.start, cur.end)
                try:
                    when = date(year, int(m.group(1)), int(m.group(2)))
                except ValueError:
                    continue
                t = Transaction(when, desc, money(m.group(4)), f"Account ...{cur.last4}")
                cur.txns.append(t)
                cur.balances.append(money(m.group(5)) if m.group(5) else None)
                open_tx = (t, [])
                continue
        if open_tx is not None:
            if not line.strip() or STOP.match(line) or not line.startswith("      "):
                close()
            else:
                open_tx[1].append(line.strip())
    close()
    for s in out:
        _reconcile(s)
    return out


def _reconcile(s: Statement) -> None:
    if s.previous is None or s.current is None:
        s.problem = "summary balances not found"
        return
    if s.previous + s.total == s.current:
        s.reconciled = True
        return
    # Some statements list a service charge or interest only in the summary. Add them only if that makes it balance exactly.
    sc, ip = s.service_charge or Decimal(0), s.interest_paid or Decimal(0)
    if (sc or ip) and s.previous + s.total + ip - sc == s.current:
        if sc:
            s.txns.append(Transaction(s.end, "Service charge (from statement summary)", -sc, f"Account ...{s.last4}"))
        if ip:
            s.txns.append(Transaction(s.end, "Interest paid (from statement summary)", ip, f"Account ...{s.last4}"))
        s.reconciled = True
        return
    s.problem = f"does not reconcile (off by {s.previous + s.total - s.current})"


def select_supersets(statements: list[Statement]) -> list[Statement]:
    """Some PDFs print the same period several times with a growing end date. Keep the longest per account and start."""
    best: dict[tuple, Statement] = {}
    for st in statements:
        k = (st.last4, st.start)
        if k not in best or (st.end, len(st.txns)) > (best[k].end, len(best[k].txns)):
            best[k] = st
    return sorted(best.values(), key=lambda x: (x.last4, x.start))


def read_pdf_lines(path: Path) -> list[str]:
    import logging

    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # rotated-text and image warnings are noise here
    r = PdfReader(str(path))
    lines: list[str] = []
    for p in r.pages:
        lines += (p.extract_text(extraction_mode="layout") or "").splitlines()
    return lines


def parse_pdf(path: Path) -> list[Statement]:
    return parse_text(read_pdf_lines(path))
