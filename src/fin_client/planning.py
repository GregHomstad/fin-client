"""Deterministic planning math. The LLM narrates these results; it never computes them.

Inputs come from profile.json (user-stated) plus cash-flow aggregates. Anything that
cannot be computed because an input is missing is listed in `missing_inputs`.
"""
from decimal import ROUND_HALF_UP, Decimal

from fin_client import tax

CENT = Decimal("0.01")
MAX_MONTHS = 1200
DEFAULT_RETURN_PCT = 6.0  # nominal long-run balanced-portfolio assumption
DEFAULT_INFLATION_PCT = 2.5
SAFE_WITHDRAWAL = 0.04
HIGH_APR_PCT = 8.0

SUGGESTED_ORDER = [
    "Starter emergency fund (about 1 month of essential spending)",
    "Capture any employer retirement match",
    "Pay off debts with APR above ~8% (highest APR first)",
    "Build emergency fund to 3-6 months of essential spending",
    "Increase retirement/investing contributions; pay down lower-APR debt in parallel",
]


def _d(x) -> Decimal:
    return Decimal(str(x))


def _c(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# --- debt payoff -----------------------------------------------------------

def _prep_debts(debts: list[dict]) -> tuple[list[dict], list[str]]:
    prepped, assumed = [], []
    for d in debts:
        bal, apr = _d(d["balance"]), _d(d["apr_pct"])
        if "min_payment" in d:
            mn = _d(d["min_payment"])
        else:
            mn = max(Decimal(25), bal * Decimal("0.02"))
            assumed.append(f"{d.get('name', 'debt')}: min_payment missing, assumed {_c(mn)} (max of $25, 2% of balance)")
        prepped.append({"name": d.get("name", "debt"), "bal": bal, "apr": apr, "min": mn})
    return prepped, assumed


def simulate_payoff(debts: list[dict], extra: Decimal, strategy: str) -> dict:
    """Month-by-month payoff. Budget = sum(minimums) + extra, constant; freed minimums roll over."""
    ds = [dict(d) for d in debts]
    budget = sum((d["min"] for d in ds), Decimal(0)) + extra
    total_interest, months, order = Decimal(0), 0, []
    while any(d["bal"] > 0 for d in ds) and months < MAX_MONTHS:
        months += 1
        for d in ds:
            if d["bal"] > 0:
                i = _c(d["bal"] * d["apr"] / 1200)
                d["bal"] += i
                total_interest += i
        left = budget
        live = [d for d in ds if d["bal"] > 0]
        for d in live:  # minimums first
            pay = min(d["min"], d["bal"])
            d["bal"] -= pay
            left -= pay
        key = (lambda d: -d["apr"]) if strategy == "avalanche" else (lambda d: d["bal"])
        for d in sorted((d for d in ds if d["bal"] > 0), key=key):
            if left <= 0:
                break
            pay = min(left, d["bal"])
            d["bal"] -= pay
            left -= pay
        for d in ds:
            if d["bal"] <= 0 and d["name"] not in order:
                order.append(d["name"])
    paid_off = all(d["bal"] <= 0 for d in ds)
    return {
        "strategy": strategy,
        "monthly_payment_total": float(_c(budget)),
        "months_to_debt_free": months if paid_off else None,
        "total_interest": float(_c(total_interest)) if paid_off else None,
        "payoff_order": order if paid_off else None,
        "note": None if paid_off else "Payments do not outpace interest; never paid off at this level.",
    }


def debt_plan(debts: list[dict], monthly_surplus: float | None) -> dict:
    prepped, assumed = _prep_debts(debts)
    total = float(_c(sum((d["bal"] for d in prepped), Decimal(0))))
    scenarios = [("minimums only", Decimal(0), "avalanche")]
    if monthly_surplus and monthly_surplus > 0:
        s = _d(monthly_surplus)
        scenarios += [
            ("avalanche, 50% of surplus extra", _c(s / 2), "avalanche"),
            ("avalanche, 100% of surplus extra", _c(s), "avalanche"),
            ("snowball, 100% of surplus extra", _c(s), "snowball"),
        ]
    out = []
    for label, extra, strat in scenarios:
        r = simulate_payoff(prepped, extra, strat)
        r["scenario"] = label
        r["extra_per_month"] = float(extra)
        out.append(r)
    return {
        "total_debt": total,
        "high_apr_debts": [d["name"] for d in prepped if d["apr"] >= Decimal(str(HIGH_APR_PCT))],
        "assumptions": assumed,
        "scenarios": out,
    }


# --- emergency fund --------------------------------------------------------

def emergency_fund(essential_monthly: float, cash_savings: float | None, monthly_contribution: float | None) -> dict:
    t3, t6 = round(essential_monthly * 3, 2), round(essential_monthly * 6, 2)
    out = {"essential_spending_per_month": round(essential_monthly, 2),
           "target_3_months": t3, "target_6_months": t6}
    if cash_savings is None:
        return out
    out["cash_savings"] = cash_savings
    out["months_covered"] = round(cash_savings / essential_monthly, 1) if essential_monthly else None
    out["shortfall_to_3_months"] = round(max(0, t3 - cash_savings), 2)
    out["shortfall_to_6_months"] = round(max(0, t6 - cash_savings), 2)
    if monthly_contribution and monthly_contribution > 0:
        out["months_to_3_at_contribution"] = -(-out["shortfall_to_3_months"] // monthly_contribution)
        out["months_to_6_at_contribution"] = -(-out["shortfall_to_6_months"] // monthly_contribution)
    return out


# --- retirement ------------------------------------------------------------

def _fv(balance: float, contrib_monthly: float, real_annual: float, years: int) -> float:
    n = years * 12
    r = (1 + real_annual) ** (1 / 12) - 1
    if abs(r) < 1e-12:
        return balance + contrib_monthly * n
    g = (1 + r) ** n
    return balance * g + contrib_monthly * (g - 1) / r


def _required_contrib(target: float, balance: float, real_annual: float, years: int) -> float:
    n = years * 12
    r = (1 + real_annual) ** (1 / 12) - 1
    if abs(r) < 1e-12:
        return max(0.0, (target - balance) / n)
    g = (1 + r) ** n
    return max(0.0, (target - balance * g) * r / (g - 1))


def retirement(p: dict) -> dict:
    """All outputs are in today's dollars (contributions assumed to rise with inflation)."""
    years = int(p["retire_age"] - p["age"])
    if years <= 0:
        return {"error": "retire_age must be greater than age"}
    base, infl = p.get("expected_return_pct", DEFAULT_RETURN_PCT), p.get("inflation_pct", DEFAULT_INFLATION_PCT)
    bal, contrib = p.get("retirement_balance", 0), p.get("monthly_retirement_contribution", 0)
    target_spend = p.get("target_retirement_spending_monthly")
    out = {"years_to_retirement": years,
           "assumptions": {"nominal_return_pct": base, "inflation_pct": infl,
                           "safe_withdrawal_rate_pct": SAFE_WITHDRAWAL * 100,
                           "dollars": "today's dollars (real)"},
           "scenarios": []}
    for label, ret in (("conservative", base - 2), ("base", base), ("optimistic", base + 2)):
        real = (1 + ret / 100) / (1 + infl / 100) - 1
        fv = _fv(bal, contrib, real, years)
        sc = {"scenario": label, "nominal_return_pct": ret, "projected_balance": round(fv),
              "sustainable_monthly_income": round(fv * SAFE_WITHDRAWAL / 12)}
        if target_spend:
            need = target_spend * 12 / SAFE_WITHDRAWAL
            sc["balance_needed"] = round(need)
            sc["on_track"] = fv >= need
            sc["monthly_contribution_needed"] = round(_required_contrib(need, bal, real, years))
        out["scenarios"].append(sc)
    return out


# --- entry point -----------------------------------------------------------

def build(profile: dict, avg_net: float | None, essential_monthly: float) -> dict:
    missing = []
    plan: dict = {"suggested_priority_order": SUGGESTED_ORDER,
                  "note": "Deterministic calculations from the code. Narrate them; do not recompute."}

    if profile.get("debts"):
        plan["debt_payoff"] = debt_plan(profile["debts"], avg_net)
    else:
        missing.append("debts (profile.json: list of {name, balance, apr_pct, min_payment}); ignore if debt-free")

    contrib_ef = profile.get("emergency_fund_monthly_contribution")
    plan["emergency_fund"] = emergency_fund(essential_monthly, profile.get("cash_savings"), contrib_ef)
    if profile.get("cash_savings") is None:
        missing.append("cash_savings (current emergency/cash balance)")

    need = [k for k in ("age", "retire_age") if k not in profile]
    if need:
        missing.append("retirement inputs: " + ", ".join(need)
                       + " (also retirement_balance, monthly_retirement_contribution, "
                         "target_retirement_spending_monthly)")
    else:
        plan["retirement"] = retirement(profile)
        for k in ("retirement_balance", "monthly_retirement_contribution", "target_retirement_spending_monthly"):
            if k not in profile:
                missing.append(f"{k} (retirement projection is more useful with it)")
    if "filing_status" in profile and "annual_gross_income" in profile:
        plan["tax"] = tax.estimate(profile)
    else:
        missing.append("tax inputs: filing_status (single|mfj|hoh|mfs) and annual_gross_income "
                       "(pre-tax; bank deposits are after-tax). Optional: traditional_401k_annual, roth_401k_annual, "
                       "traditional_ira_annual, roth_ira_annual, itemized_deductions")
    plan["missing_inputs"] = missing
    return plan
