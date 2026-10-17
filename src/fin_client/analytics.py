"""Cash-flow analytics over categorized transactions."""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from fin_client.categorize import Rules, categorize
from fin_client.models import Transaction

EXCLUDED = {"Transfers"}  # internal movements don't count as income/spending


@dataclass
class Month:
    month: str
    income: Decimal = Decimal(0)
    spending: Decimal = Decimal(0)  # positive number
    by_category: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    @property
    def net(self) -> Decimal:
        return self.income - self.spending

    @property
    def savings_rate(self) -> Decimal | None:
        if self.income <= 0:
            return None
        return (self.net / self.income * 100).quantize(Decimal("0.1"))


def cashflow(txns: list[Transaction], rules: Rules | None = None) -> list[Month]:
    months: dict[str, Month] = {}
    for t in txns:
        cat = categorize(t.description, t.amount, rules, t.bank_category)
        if cat in EXCLUDED:
            continue
        m = months.setdefault(t.date.isoformat()[:7], Month(t.date.isoformat()[:7]))
        if t.amount > 0:
            m.income += t.amount
        else:
            m.spending += -t.amount
            m.by_category[cat] += -t.amount
    return [months[k] for k in sorted(months)]


def averages(months: list[Month]) -> dict[str, Decimal]:
    """Averages over full months; drops the first and last month if there are 3+ (likely partial)."""
    use = months[1:-1] if len(months) >= 3 else months
    if not use:
        return {}
    n = Decimal(len(use))
    inc = sum((m.income for m in use), Decimal(0)) / n
    spend = sum((m.spending for m in use), Decimal(0)) / n
    q = Decimal("0.01")
    return {
        "months_used": n,
        "avg_income": inc.quantize(q),
        "avg_spending": spend.quantize(q),
        "avg_net": (inc - spend).quantize(q),
    }
