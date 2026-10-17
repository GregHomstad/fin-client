"""Detect recurring charges and deposits, say which are still active, and flag the ones that stopped.

A charge is "active" if its latest payment is recent enough for its cadence, measured against the newest data
for the SAME account (so an account whose export stops on the 20th is not blamed for a charge due on the 25th).
A charge that stopped is flagged for follow-up: a payment that just ends can mean a cancelled plan with the
account still open, a failed payment, or a closed card.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from fin_client.merchants import normalize
from fin_client.models import Transaction

CADENCES = {  # name: (days, tolerance, occurrences per month)
    "weekly": (7, 1, Decimal("4.345")),
    "biweekly": (14, 2, Decimal("2.173")),
    "monthly": (30, 4, Decimal(1)),
    "quarterly": (91, 8, Decimal(1) / 3),
    "yearly": (365, 15, Decimal(1) / 12),
}


@dataclass(frozen=True)
class Recurring:
    merchant: str
    cadence: str
    amount: Decimal  # typical (median), signed
    count: int
    last_date: date
    next_expected: date
    first_date: date | None = None
    account: str = ""
    status: str = "active"  # active | stopped
    days_since_last: int = 0

    @property
    def monthly_cost(self) -> Decimal:
        return (self.amount * CADENCES[self.cadence][2]).quantize(Decimal("0.01"))

    @property
    def follow_up(self) -> str:
        if self.status != "stopped":
            return ""
        what = "deposit" if self.amount > 0 else "charge"
        return (f"Last {what} {self.last_date.isoformat()}. Confirm the plan is cancelled AND the account itself is closed or deleted "
                "(a payment that just stops can be a cancelled plan with the account still open, a failed payment, or a closed card).")


def merchant_key(description: str) -> str:
    return normalize(description, max_tokens=4)


GENERIC = {"PAYMENT", "PAYMENTS", "PYMNT", "PYMNTS", "TRANSFER", "ONLINE", "MOBILE", "WITHDRAWAL", "DEPOSIT", "PURCHASE", "SERVICE", "CHARGE",
           "ACCOUNT", "BANK", "CARD", "DEBIT", "CREDIT", "WEB", "BILL", "AUTOPAY"}


def _tail(key: str) -> str:
    last = key.split()[-1] if key else ""
    return last if len(last) >= 3 else ""


def _same_biller(a: str, b: str) -> bool:
    """Same last word, or a shared distinctive word (a name of at least 5 letters that is not a generic banking word)."""
    if _tail(a) and _tail(a) == _tail(b):
        return True
    return bool({w for w in a.split() if len(w) >= 5 and w not in GENERIC} & {w for w in b.split() if len(w) >= 5 and w not in GENERIC})


def _merge_aliases(groups: dict[tuple[str, bool], list[Transaction]]) -> dict[tuple[str, bool], list[Transaction]]:
    """A biller can change how it shows up (a new payment channel, a web reference instead of your name).

    Groups with the same last word, similar amounts and date ranges that follow each other are one recurring payment.
    """
    merged = dict(groups)
    changed = True
    while changed:
        changed = False
        keys = list(merged)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if a not in merged or b not in merged or a[1] != b[1] or not _same_biller(a[0], b[0]):
                    continue
                ga, gb = sorted(merged[a], key=lambda t: t.date), sorted(merged[b], key=lambda t: t.date)
                ma, mb = median([t.amount for t in ga]), median([t.amount for t in gb])
                if not ma or abs(ma - mb) > abs(ma) * Decimal("0.15"):
                    continue
                first, second = (ga, gb) if ga[0].date <= gb[0].date else (gb, ga)
                if first[-1].date <= second[0].date and (second[0].date - first[-1].date).days <= 60:
                    merged[a] = ga + gb
                    del merged[b]
                    changed = True
                    break
            if changed:
                break
    return merged


def detect(txns: list[Transaction], min_occurrences: int = 3, amount_tol: float = 0.15) -> list[Recurring]:
    """Every recurring pattern in the history. Yearly ones need only two payments about a year apart."""
    groups: dict[tuple[str, bool], list[Transaction]] = defaultdict(list)
    newest: dict[str, date] = {}
    for t in txns:
        newest[t.account] = max(newest.get(t.account, t.date), t.date)
        key = merchant_key(t.description)
        if key:
            groups[(key, t.amount > 0)].append(t)
    overall = max(newest.values(), default=date.today())
    groups = _merge_aliases(groups)
    found = []
    for (key, _), items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda t: t.date)
        amts = [t.amount for t in items]
        med = median(amts)
        if med == 0 or any(abs(a - med) > abs(med) * Decimal(str(amount_tol)) for a in amts):
            continue
        gap = median([(b.date - a.date).days for a, b in zip(items, items[1:])])
        for name, (days, tol, _) in CADENCES.items():
            if abs(gap - days) <= tol and (len(items) >= min_occurrences or name == "yearly"):
                last = items[-1]
                ref = newest.get(last.account, overall)
                age = (ref - last.date).days
                active = age <= days * 1.3 + tol
                found.append(Recurring(key, name, med, len(items), last.date, last.date + timedelta(days=days), items[0].date,
                                       last.account, "active" if active else "stopped", age))
                break
    return sorted(found, key=lambda r: r.amount)


def analyze(txns: list[Transaction], since: date | None = None, not_a_commitment=None) -> dict[str, list[Recurring]]:
    """Group the patterns for display.

    active   - still charging, with a payment since `since` (your baseline month), excluding yearly ones
    yearly   - once-a-year charges and when the next renewal is due, whenever the last one was
    stopped  - patterns that ended: follow up to make sure the plan is cancelled and the account closed
    """
    items = detect(txns)
    active = [r for r in items if r.status == "active" and r.cadence != "yearly" and (since is None or r.last_date >= since)]
    yearly = [r for r in items if r.cadence == "yearly" and r.status == "active"]
    # a restaurant you visit monthly is a habit, not an account to close: only commitments get a follow-up
    stopped = [r for r in items if r.status == "stopped" and not (not_a_commitment and not_a_commitment(r))]
    return {"active": active, "yearly": yearly, "stopped": sorted(stopped, key=lambda r: r.last_date, reverse=True)}
