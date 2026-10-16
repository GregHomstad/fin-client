"""Detect recurring charges/deposits from transaction history."""
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

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

    @property
    def monthly_cost(self) -> Decimal:
        return (self.amount * CADENCES[self.cadence][2]).quantize(Decimal("0.01"))


def merchant_key(description: str) -> str:
    s = re.sub(r"[#*]?\d+", " ", description.upper())
    s = re.sub(r"[^A-Z& ]", " ", s)
    return " ".join(s.split())


def detect(txns: list[Transaction], min_occurrences: int = 3, amount_tol: float = 0.15) -> list[Recurring]:
    groups: dict[tuple[str, bool], list[Transaction]] = defaultdict(list)
    for t in txns:
        key = merchant_key(t.description)
        if key:
            groups[(key, t.amount > 0)].append(t)
    found = []
    for (key, _), items in groups.items():
        if len(items) < min_occurrences:
            continue
        items.sort(key=lambda t: t.date)
        amts = [t.amount for t in items]
        med = median(amts)
        if med == 0 or any(abs(a - med) > abs(med) * Decimal(str(amount_tol)) for a in amts):
            continue
        gaps = [(b.date - a.date).days for a, b in zip(items, items[1:])]
        gap = median(gaps)
        for name, (days, tol, _) in CADENCES.items():
            if abs(gap - days) <= tol:
                last = items[-1].date
                found.append(Recurring(key, name, med, len(items), last, last + timedelta(days=days)))
                break
    return sorted(found, key=lambda r: r.amount)
