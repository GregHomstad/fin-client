"""Build the advisor's grounding context: computed aggregates only, never raw rows.

Merchant names appear only for detected recurring commitments. Everything else is
category-level totals. Optional user-supplied facts come from profile.json.
"""
import json
from decimal import Decimal

from fin_client import analytics, planning, recurring
from fin_client.categorize import Rules
from fin_client.models import Transaction
from fin_client.paths import data_dir

ESSENTIALS = {"Housing", "Utilities", "Groceries", "Insurance", "Health", "Transportation"}
Q = Decimal("0.01")


def _f(x: Decimal) -> float:
    return float(x.quantize(Q))


def load_profile() -> dict:
    """User-stated facts (age, debts, balances, goals) from profile.json in the data dir."""
    path = data_dir() / "profile.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def profile_for(conn) -> dict:
    """profile.json merged with what the accounts already know (debts, cash, retirement balances)."""
    from fin_client import accounts
    return accounts.effective_profile(load_profile(), accounts.list_accounts(conn))


def build(txns: list[Transaction], rules: Rules | None = None, profile: dict | None = None) -> dict:
    months = analytics.cashflow(txns, rules)
    if not months:
        return {"error": "No transactions imported yet."}
    full = months[1:-1] if len(months) >= 3 else months
    n = Decimal(len(full))
    avg = analytics.averages(months)

    cat_totals: dict[str, Decimal] = {}
    for m in full:
        for c, v in m.by_category.items():
            cat_totals[c] = cat_totals.get(c, Decimal(0)) + v
    cat_avg = {c: _f(v / n) for c, v in sorted(cat_totals.items(), key=lambda kv: -kv[1])}

    essential = sum((v for c, v in cat_totals.items() if c in ESSENTIALS), Decimal(0)) / n
    income = avg["avg_income"]
    fixed = recurring.detect(txns)
    fixed_out = -sum((r.monthly_cost for r in fixed if r.amount < 0), Decimal(0))

    plan = planning.build(profile or {}, _f(avg["avg_net"]), _f(essential))
    recon = None
    t = plan.get("tax")
    if t and "error" not in t:
        est = t["estimated_monthly_take_home"]
        recon = {"estimated_monthly_take_home": est, "bank_income_per_month": _f(income),
                 "difference": round(_f(income) - est, 2),
                 "note": "Bank income may include non-pay deposits; a large gap suggests missing deductions "
                         "(e.g. health premiums, other withholding) or wrong gross income in profile.json."}
    return {
        "period": {"first_month": months[0].month, "last_month": months[-1].month,
                   "months_in_averages": int(n)},
        "monthly_averages": {
            "income": _f(income), "spending": _f(avg["avg_spending"]), "net": _f(avg["avg_net"]),
            "savings_rate_pct": _f(avg["avg_net"] / income * 100) if income > 0 else None,
        },
        "average_spending_by_category": cat_avg,
        "essential_spending_per_month": _f(essential),
        "recurring_outflows_per_month": _f(fixed_out),
        "recurring_outflows_pct_of_income": _f(fixed_out / income * 100) if income > 0 else None,
        "recurring_items": [
            {"merchant": r.merchant, "cadence": r.cadence, "amount": _f(r.amount)} for r in fixed
        ],
        "monthly_history": [
            {"month": m.month, "income": _f(m.income), "spending": _f(m.spending),
             "net": _f(m.net)} for m in months
        ],
        "planning": plan,
        "paycheck_reconciliation": recon,
        "user_profile": profile or {},
        "notes": ["Internal transfers are excluded from income and spending.",
                  "First and last months are excluded from averages when 3+ months exist."],
    }
