"""Federal income tax estimator with 401(k)/IRA effects. Deterministic; the LLM only narrates it.

Scope: ordinary federal income tax on wages, standard vs itemized deduction, pre-tax 401(k),
traditional IRA deduction phase-outs, Roth IRA eligibility. NOT modeled: FICA, state tax, credits,
capital gains, AMT, NIIT, self-employment, HSA. Annual figures are year-keyed in DATA.

Sources (verified against the primary documents):
  2026 brackets, standard deduction, aged/blind add-on: IRS Rev. Proc. 2025-32 (sections 4.01, 4.14)
  2026 401(k)/IRA limits and phase-outs: IRS news release IR-2025-111 (Notice 2025-67)
"""
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

D = Decimal
INF = None  # top bracket has no upper bound


def _b(*pairs):
    return [(D(lo), D(str(rate))) for lo, rate in pairs]


# Each bracket: (lower bound of taxable income, rate)
DATA = {
    2026: {
        "brackets": {
            "single": _b((0, .10), (12400, .12), (50400, .22), (105700, .24), (201775, .32), (256225, .35), (640600, .37)),
            "mfj": _b((0, .10), (24800, .12), (100800, .22), (211400, .24), (403550, .32), (512450, .35), (768700, .37)),
            "hoh": _b((0, .10), (17700, .12), (67450, .22), (105700, .24), (201750, .32), (256200, .35), (640600, .37)),
            "mfs": _b((0, .10), (12400, .12), (50400, .22), (105700, .24), (201775, .32), (256225, .35), (384350, .37)),
        },
        "standard_deduction": {"single": 16100, "mfj": 32200, "hoh": 24150, "mfs": 16100},
        "aged_addon": {"married": 1650, "unmarried": 2050},
        "limit_401k": 24500, "catchup_401k_50": 8000, "catchup_401k_60_63": 11250,
        "limit_ira": 7500, "catchup_ira_50": 1100,
        # (start, end) of MAGI phase-out
        "trad_ira_deduction_covered": {"single": (81000, 91000), "hoh": (81000, 91000),
                                       "mfj": (129000, 149000), "mfs": (0, 10000)},
        "trad_ira_deduction_spouse_covered_only": (242000, 252000),
        "roth_ira_phaseout": {"single": (153000, 168000), "hoh": (153000, 168000),
                              "mfj": (242000, 252000), "mfs": (0, 10000)},
    },
}
DEFAULT_YEAR = 2026
STATUSES = {"single", "mfj", "hoh", "mfs"}
Q = D("0.01")


def _r(x: D) -> float:
    return float(x.quantize(Q, rounding=ROUND_HALF_UP))


def bracket_tax(taxable: D, brackets: list[tuple[D, D]]) -> D:
    tax = D(0)
    for i, (lo, rate) in enumerate(brackets):
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else None
        if taxable <= lo:
            break
        tax += ((min(taxable, hi) if hi is not None else taxable) - lo) * rate
    return tax


def marginal(taxable: D, brackets: list[tuple[D, D]]) -> tuple[D, D | None]:
    """(marginal rate, dollars of taxable income left before the next bracket or None at the top)."""
    for i in range(len(brackets) - 1, -1, -1):
        lo, rate = brackets[i]
        if taxable > lo or i == 0:
            nxt = brackets[i + 1][0] if i + 1 < len(brackets) else None
            return rate, (nxt - taxable if nxt is not None else None)
    raise AssertionError


def _phaseout_fraction(magi: D, start: int, end: int) -> D:
    """Fraction of the full limit still allowed (1 = full, 0 = none)."""
    if magi <= start:
        return D(1)
    if magi >= end:
        return D(0)
    return (D(end) - magi) / D(end - start)


def _reduced_limit(limit: D, magi: D, start: int, end: int) -> D:
    f = _phaseout_fraction(magi, start, end)
    if f == 1:
        return limit
    if f == 0:
        return D(0)
    reduced = (limit * f / 10).to_integral_value(rounding=ROUND_CEILING) * 10  # IRS rounds up to $10
    return max(reduced, D(200))


def limits_for(year_data: dict, age: int | None) -> tuple[D, D]:
    l401 = D(year_data["limit_401k"])
    lira = D(year_data["limit_ira"])
    if age is not None:
        if 60 <= age <= 63:
            l401 += year_data["catchup_401k_60_63"]
        elif age >= 50:
            l401 += year_data["catchup_401k_50"]
        if age >= 50:
            lira += year_data["catchup_ira_50"]
    return l401, lira


def _compute(y: dict, status: str, gross: D, age: int | None, trad_401k: D, roth_401k: D,
             trad_ira: D, roth_ira: D, itemized: D, covered: bool, spouse_covered: bool) -> dict:
    brackets = y["brackets"][status]
    ira_limit = limits_for(y, age)[1]
    magi_pre_ira = gross - trad_401k

    # Traditional IRA deductibility
    if covered:
        start, end = y["trad_ira_deduction_covered"][status]
        allowed = _reduced_limit(ira_limit, magi_pre_ira, start, end)
    elif status == "mfj" and spouse_covered:
        start, end = y["trad_ira_deduction_spouse_covered_only"]
        allowed = _reduced_limit(ira_limit, magi_pre_ira, start, end)
    else:
        allowed = ira_limit
    ira_deduction = min(trad_ira, allowed)

    agi = gross - trad_401k - ira_deduction
    std = D(y["standard_deduction"][status])
    if age is not None and age >= 65:
        std += y["aged_addon"]["unmarried" if status in ("single", "hoh") else "married"]
    deduction = max(std, itemized)
    taxable = max(D(0), agi - deduction)
    tax = bracket_tax(taxable, brackets)
    rate, headroom = marginal(taxable, brackets)
    return {"agi": agi, "deduction": deduction, "deduction_type": "itemized" if itemized > std else "standard",
            "taxable_income": taxable, "tax": tax, "marginal_rate": rate, "headroom": headroom,
            "ira_deduction": ira_deduction, "ira_deduction_allowed": allowed, "magi_pre_ira": magi_pre_ira}


def estimate(profile: dict, year: int = DEFAULT_YEAR) -> dict:
    """Needs profile: filing_status, annual_gross_income. Everything else optional."""
    if year not in DATA:
        return {"error": f"No tax data for {year}; add it to tax.DATA (available: {sorted(DATA)})"}
    y = DATA[year]
    status = str(profile.get("filing_status", "")).lower()
    if status not in STATUSES:
        return {"error": f"filing_status must be one of {sorted(STATUSES)}"}
    if "annual_gross_income" not in profile:
        return {"error": "annual_gross_income missing (bank deposits are after-tax, so gross must come from you)"}

    gross = D(str(profile["annual_gross_income"]))
    age = profile.get("age")
    t401, r401 = D(str(profile.get("traditional_401k_annual", 0))), D(str(profile.get("roth_401k_annual", 0)))
    tira, rira = D(str(profile.get("traditional_ira_annual", 0))), D(str(profile.get("roth_ira_annual", 0)))
    itemized = D(str(profile.get("itemized_deductions", 0)))
    covered = bool(profile.get("covered_by_workplace_plan", t401 + r401 > 0))
    spouse_covered = bool(profile.get("spouse_covered_by_workplace_plan", False))
    args = (y, status, gross, age)

    def run(t401_, tira_):
        return _compute(*args, t401_, r401, tira_, rira, itemized, covered, spouse_covered)

    cur = run(t401, tira)
    l401, lira = limits_for(y, age)
    warnings = []
    if t401 + r401 > l401:
        warnings.append(f"401(k) contributions {t401 + r401} exceed the {year} limit of {l401}")
    if tira + rira > lira:
        warnings.append(f"IRA contributions {tira + rira} exceed the {year} limit of {lira}")
    if age is None:
        warnings.append("age not provided: 401(k)/IRA catch-up and 65+ standard deduction not applied")

    # Roth IRA eligibility
    rs, re_ = y["roth_ira_phaseout"][status]
    roth_allowed = _reduced_limit(lira, cur["agi"], rs, re_)
    roth_status = "full" if roth_allowed == lira else ("none" if roth_allowed == 0 else "partial")
    if rira > roth_allowed:
        warnings.append(f"Roth IRA contribution {rira} exceeds the income-limited maximum of {roth_allowed}")

    # What-if scenarios (additional traditional contributions reduce this year's tax)
    room_401 = max(D(0), l401 - t401 - r401)
    room_ira = max(D(0), lira - tira - rira)
    scenarios = []
    base_tax = cur["tax"]

    def scen(label, t401_, tira_):
        s = run(t401_, tira_)
        extra = (t401_ - t401) + (s["ira_deduction"] - cur["ira_deduction"])
        saved = base_tax - s["tax"]
        scenarios.append({"scenario": label, "extra_pre_tax_contribution": _r(extra),
                          "federal_tax": _r(s["tax"]), "federal_tax_saved": _r(saved),
                          "net_take_home_cost": _r(extra - saved),
                          "new_marginal_rate_pct": _r(s["marginal_rate"] * 100)})

    if room_401 > 0:
        scen("max out traditional 401(k)", t401 + room_401, tira)
    if room_ira > 0 and cur["ira_deduction_allowed"] > tira:
        scen("fill deductible traditional IRA", t401, tira + min(room_ira, cur["ira_deduction_allowed"] - tira))
    if room_401 > 0 and room_ira > 0 and cur["ira_deduction_allowed"] > tira:
        scen("both", t401 + room_401, tira + min(room_ira, cur["ira_deduction_allowed"] - tira))

    return {
        "tax_year": year, "filing_status": status,
        "gross_income": _r(gross),
        "pre_tax_401k": _r(t401), "adjusted_gross_income": _r(cur["agi"]),
        "deduction_used": {"type": cur["deduction_type"], "amount": _r(cur["deduction"])},
        "taxable_income": _r(cur["taxable_income"]),
        "federal_income_tax": _r(cur["tax"]),
        "effective_rate_on_gross_pct": _r(cur["tax"] / gross * 100) if gross else None,
        "marginal_rate_pct": _r(cur["marginal_rate"] * 100),
        "taxable_income_until_next_bracket": _r(cur["headroom"]) if cur["headroom"] is not None else None,
        "estimated_monthly_federal_tax": _r(cur["tax"] / 12),
        "retirement": {
            "limit_401k": _r(l401), "room_left_401k": _r(room_401),
            "limit_ira": _r(lira), "room_left_ira": _r(room_ira),
            "traditional_ira_deductible_max": _r(cur["ira_deduction_allowed"]),
            "roth_ira_eligibility": roth_status, "roth_ira_max_allowed": _r(roth_allowed),
        },
        "what_if": scenarios,
        "warnings": warnings,
        "not_modeled": ["FICA/payroll tax", "state tax", "tax credits", "capital gains", "AMT/NIIT",
                        "self-employment", "HSA"],
        "note": "Traditional contributions cut tax now but are taxed at withdrawal; Roth contributions give no "
                "deduction now but grow tax-free. The better choice depends on current vs expected retirement rate.",
    }
