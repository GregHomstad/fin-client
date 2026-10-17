"""Match money moving between your own accounts so it is not counted as income or spending.

A card payment shows up twice: money out of checking and money into the card. Both legs are the same
amount, on different accounts, a day or two apart. Marked pairs get the bank category "Transfer (matched)".
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from fin_client.models import Transaction

HINTS = ("TRANSFER", "PAYMENT", "PYMT", "AUTOPAY", "AUTO PAY", "ACH", "ZELLE", "VENMO", "XFER", "THANK YOU", "ONLINE")
MARK = "Transfer (matched)"


def _hinted(t: Transaction) -> bool:
    text = f"{t.description} {t.bank_category}".upper()
    return any(h in text for h in HINTS)


def mark_internal(txns: list[Transaction], window_days: int = 4) -> list[Transaction]:
    outs = [(i, t) for i, t in enumerate(txns) if t.amount < 0]
    ins = [(i, t) for i, t in enumerate(txns) if t.amount > 0]
    by_amount: dict[Decimal, list[tuple[int, Transaction]]] = {}
    for i, t in ins:
        by_amount.setdefault(t.amount, []).append((i, t))
    matched: set[int] = set()
    for i, o in sorted(outs, key=lambda x: x[1].date):
        best = None
        for j, p in by_amount.get(-o.amount, []):
            if j in matched or p.account == o.account:
                continue
            gap = abs((p.date - o.date).days)
            if gap > window_days or not (gap <= 1 or _hinted(o) or _hinted(p)):
                continue
            if best is None or gap < best[0]:
                best = (gap, j)
        if best:
            matched.update((i, best[1]))
    return [replace(t, bank_category=MARK) if i in matched else t for i, t in enumerate(txns)]
