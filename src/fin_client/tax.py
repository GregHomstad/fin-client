"""Federal income tax estimator with 401(k)/IRA effects. Deterministic; the LLM only narrates it.

Scope: ordinary federal income tax on wages, standard vs itemized deduction, pre-tax 401(k),
traditional IRA deduction phase-outs, Roth IRA eligibility, employee FICA, Arkansas income tax.
NOT modeled: other states, credits (except AR personal credits), capital gains, AMT, NIIT,
self-employment, HSA. Annual figures are year-keyed in DATA / STATE_DATA.

Sources:
  2026 brackets, standard deduction, aged/blind add-on: IRS Rev. Proc. 2025-32 (sections 4.01, 4.14) [primary]
  2026 401(k)/IRA limits and phase-outs: IRS news release IR-2025-111 / Notice 2025-67 [primary]
  2026 Arkansas brackets, $2,470 standard deduction, $29 credit: AR DFA Withholding Tax Formula Method,
    effective 01/01/2026 (reflects Act 2 of the 2026 First Extraordinary Session: top rate 3.7%) [primary]
  2026 Social Security wage base $184,500 and Additional Medicare thresholds: SSA fact sheet via secondary
    sources (thresholds are statutory and unindexed) [secondary; verify]
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
FICA = {2026: {"ss_rate": "0.062", "ss_wage_base": 184500, "medicare_rate": "0.0145",
               "addl_medicare_rate": "0.009",
               "addl_medicare_threshold": {"single": 200000, "hoh": 200000, "mfj": 250000, "mfs": 125000}}}

# Arkansas: tax = rate * x - adjustment, on net taxable income x (DFA formula brackets)
STATE_DATA = {
    2026: {"AR": {
        "standard_deduction": 2470, "mfj_two_earner_standard_deduction": 4940, "personal_credit": 29,
        "brackets": [(0, 5599, "0", "0"), (5600, 11199, "0.02", "111.98"), (11200, 15999, "0.03", "223.97"),
                     (16000, 26399, "0.034", "287.97"), (26400, 94700, "0.037", "367.16")],
        "high_rate": "0.037", "high_base_adjustment": "79.90",  # above 94,700: 3.7% x - 79.90 - phase-out credit
        "phaseout_start": 94701, "phaseout_end": 97600, "phaseout_max": 290, "phaseout_step": 10,
        "midrange_below": 97701,
    }},
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


def ar_tax(net_taxable: D, exemptions: int, year: int) -> D:
    """Arkansas tax per the DFA formula: $100-band midrange lookup below 97,701, rounded to whole dollars."""
    a = STATE_DATA[year]["AR"]
    x = max(D(0), net_taxable)
    if x < a["midrange_below"]:
        x = D(int((x - 1) // 100) * 100 + 50) if x > 0 else D(0)
    gross = None
    for lo, hi, rate, adj in a["brackets"]:
        if lo <= x <= hi or (lo == 0 and x <= hi):
            gross = x * D(rate) - D(adj)
            break
    if gross is None:  # above 94,700
        credit = D(0)
        if a["phaseout_start"] <= x <= a["phaseout_end"]:
            steps = int((x - a["phaseout_start"]) // 100)
            credit = D(max(0, a["phaseout_max"] - a["phaseout_step"] * steps))
        gross = x * D(a["high_rate"]) - D(a["high_base_adjustment"]) - credit
    gross = max(D(0), gross).quantize(D(1), rounding=ROUND_HALF_UP)
    return max(D(0), gross - D(a["personal_credit"]) * exemptions)


def ar_marginal(net_taxable: D, year: int) -> D:
    a = STATE_DATA[year]["AR"]
    x = max(D(0), net_taxable)
    for lo, hi, rate, _ in a["brackets"]:
        if x <= hi:
            return D(rate)
    return D(a["high_rate"])


def fica(status: str, wages: list[D], year: int) -> dict:
    f = FICA[year]
    ss = sum((min(w, D(f["ss_wage_base"])) * D(f["ss_rate"]) for w in wages), D(0))
    med = sum(wages, D(0)) * D(f["medicare_rate"])
    thr = D(f["addl_medicare_threshold"][status])
    addl = max(D(0), sum(wages, D(0)) - thr) * D(f["addl_medicare_rate"])
    return {"social_security": ss, "medicare": med, "additional_medicare": addl, "total": ss + med + addl}


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
             trad_ira: D, roth_ira: D, itemized: D, covered: bool, spouse_covered: bool,
             state: str | None = None, ar_ded: D = D(0), ar_exemptions: int = 1, year: int = DEFAULT_YEAR) -> dict:
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
    st_tax, st_net, st_rate = D(0), D(0), D(0)
    if state == "AR":
        st_net = max(D(0), agi - ar_ded)
        st_tax = ar_tax(st_net, ar_exemptions, year)
        st_rate = ar_marginal(st_net, year)
    return {"state_tax": st_tax, "state_taxable": st_net, "state_marginal": st_rate,
            "agi": agi, "deduction": deduction, "deduction_type": "itemized" if itemized > std else "standard",
            "taxable_income": taxable, "tax": tax, "marginal_rate": rate, "headroom": headroom,
            "ira_deduction": ira_deduction, "ira_deduction_allowed": allowed, "magi_pre_ira": magi_pre_ira}


def estimate(profile: dict, year: int = DEFAULT_YEAR) -> dict:
    """Needs profile: filing_status, annual_gross_income. Everything else optional.

    Optional: age, traditional_401k_annual, roth_401k_annual, traditional_ira_annual, roth_ira_annual,
    itemized_deductions, pre_tax_benefits_annual (health premiums/FSA: cut income-tax AND FICA wages),
    state ("AR" default, or "none"), ar_exemptions, spouse_has_income, fica_earner_wages (list, for 2 earners).
    """
    if year not in DATA or year not in FICA:
        return {"error": f"No tax data for {year}; add it to tax.DATA/FICA (available: {sorted(DATA)})"}
    y = DATA[year]
    status = str(profile.get("filing_status", "")).lower()
    if status not in STATUSES:
        return {"error": f"filing_status must be one of {sorted(STATUSES)}"}
    if "annual_gross_income" not in profile:
        return {"error": "annual_gross_income missing (bank deposits are after-tax, so gross must come from you)"}

    gross = D(str(profile["annual_gross_income"]))
    benefits = D(str(profile.get("pre_tax_benefits_annual", 0)))
    wages = gross - benefits  # wages for income tax and FICA after section 125 benefits
    age = profile.get("age")
    t401, r401 = D(str(profile.get("traditional_401k_annual", 0))), D(str(profile.get("roth_401k_annual", 0)))
    tira, rira = D(str(profile.get("traditional_ira_annual", 0))), D(str(profile.get("roth_ira_annual", 0)))
    itemized = D(str(profile.get("itemized_deductions", 0)))
    covered = bool(profile.get("covered_by_workplace_plan", t401 + r401 > 0))
    spouse_covered = bool(profile.get("spouse_covered_by_workplace_plan", False))

    warnings = []
    state = str(profile.get("state", "AR")).upper()
    if state not in ("AR", "NONE"):
        warnings.append(f"state {state} is not modeled; state tax omitted (only AR is supported)")
    state = state if state == "AR" else None
    ar = STATE_DATA[year]["AR"]
    ar_ded = D(ar["mfj_two_earner_standard_deduction"] if status == "mfj" and profile.get("spouse_has_income")
               else ar["standard_deduction"])
    ar_ex = int(profile.get("ar_exemptions", 2 if status == "mfj" else 1))

    def run(t401_, tira_):
        return _compute(y, status, wages, age, t401_, r401, tira_, rira, itemized, covered, spouse_covered,
                        state, ar_ded, ar_ex, year)

    cur = run(t401, tira)
    l401, lira = limits_for(y, age)
    if t401 + r401 > l401:
        warnings.append(f"401(k) contributions {t401 + r401} exceed the {year} limit of {l401}")
    if tira + rira > lira:
        warnings.append(f"IRA contributions {tira + rira} exceed the {year} limit of {lira}")
    if age is None:
        warnings.append("age not provided: 401(k)/IRA catch-up and 65+ standard deduction not applied")

    # FICA (401(k) deferrals do NOT reduce FICA wages)
    earners = [D(str(w)) for w in profile.get("fica_earner_wages", [])] or [wages]
    fi = fica(status, earners, year)

    # Roth IRA eligibility
    rs, re_ = y["roth_ira_phaseout"][status]
    roth_allowed = _reduced_limit(lira, cur["agi"], rs, re_)
    roth_status = "full" if roth_allowed == lira else ("none" if roth_allowed == 0 else "partial")
    if rira > roth_allowed:
        warnings.append(f"Roth IRA contribution {rira} exceeds the income-limited maximum of {roth_allowed}")

    # What-if scenarios (additional traditional contributions reduce this year's income tax; FICA unchanged)
    room_401 = max(D(0), l401 - t401 - r401)
    room_ira = max(D(0), lira - tira - rira)
    scenarios = []

    def scen(label, t401_, tira_):
        s = run(t401_, tira_)
        extra = (t401_ - t401) + (s["ira_deduction"] - cur["ira_deduction"])
        fed_saved, st_saved = cur["tax"] - s["tax"], cur["state_tax"] - s["state_tax"]
        scenarios.append({"scenario": label, "extra_pre_tax_contribution": _r(extra),
                          "federal_tax": _r(s["tax"]), "federal_tax_saved": _r(fed_saved),
                          "state_tax_saved": _r(st_saved), "total_tax_saved": _r(fed_saved + st_saved),
                          "net_take_home_cost": _r(extra - fed_saved - st_saved),
                          "new_marginal_rate_pct": _r(s["marginal_rate"] * 100)})

    if room_401 > 0:
        scen("max out traditional 401(k)", t401 + room_401, tira)
    if room_ira > 0 and cur["ira_deduction_allowed"] > tira:
        scen("fill deductible traditional IRA", t401, tira + min(room_ira, cur["ira_deduction_allowed"] - tira))
    if room_401 > 0 and room_ira > 0 and cur["ira_deduction_allowed"] > tira:
        scen("both", t401 + room_401, tira + min(room_ira, cur["ira_deduction_allowed"] - tira))

    total_tax = cur["tax"] + cur["state_tax"] + fi["total"]
    take_home = gross - benefits - t401 - r401 - total_tax  # IRA contributions come from after-pay money
    ss_below_base = max(earners) < D(FICA[year]["ss_wage_base"])
    payroll_marginal = (D(FICA[year]["ss_rate"]) if ss_below_base else D(0)) + D(FICA[year]["medicare_rate"])
    out = {
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
        "fica": {k: _r(v) for k, v in fi.items()},
        "state": ({"state": "AR", "taxable_income": _r(cur["state_taxable"]), "income_tax": _r(cur["state_tax"]),
                   "marginal_rate_pct": _r(cur["state_marginal"] * 100),
                   "standard_deduction": _r(ar_ded), "personal_credits": _r(D(ar["personal_credit"]) * ar_ex)}
                  if state else None),
        "total_taxes": _r(total_tax),
        "total_effective_rate_pct": _r(total_tax / gross * 100) if gross else None,
        "combined_marginal_rate_on_next_dollar_pct": _r(
            (cur["marginal_rate"] + cur["state_marginal"] + payroll_marginal) * 100),
        "estimated_annual_take_home": _r(take_home),
        "estimated_monthly_take_home": _r(take_home / 12),
        "retirement": {
            "limit_401k": _r(l401), "room_left_401k": _r(room_401),
            "limit_ira": _r(lira), "room_left_ira": _r(room_ira),
            "traditional_ira_deductible_max": _r(cur["ira_deduction_allowed"]),
            "roth_ira_eligibility": roth_status, "roth_ira_max_allowed": _r(roth_allowed),
        },
        "what_if": scenarios,
        "warnings": warnings,
        "not_modeled": ["other states", "tax credits (except AR personal credits)", "capital gains", "AMT/NIIT",
                        "self-employment", "HSA", "employer-side payroll tax", "AR itemized deductions/retirement exclusions"],
        "note": "Traditional contributions cut income tax now but are taxed at withdrawal; Roth contributions give no "
                "deduction now but grow tax-free. 401(k) deferrals do not reduce FICA. The better choice depends on "
                "current vs expected retirement tax rate.",
    }
    return out
