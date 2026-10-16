import json
from decimal import Decimal as D

import pytest

from fin_client import cli, planning, tax

B = tax.DATA[2026]["brackets"]

# (taxable income, tax) pairs taken from the IRS Rev. Proc. 2025-32 section 4.01 tables
IRS_VECTORS = {
    "single": [(12400, "1240"), (50400, "5800"), (105700, "17966"), (201775, "41024"),
               (256225, "58448"), (640600, "192979.25")],
    "mfj": [(24800, "2480"), (100800, "11600"), (211400, "35932"), (403550, "82048"),
            (512450, "116896"), (768700, "206583.50")],
    "hoh": [(17700, "1770"), (67450, "7740"), (105700, "16155"), (201750, "39207"),
            (256200, "56631"), (640600, "191171")],
    "mfs": [(12400, "1240"), (50400, "5800"), (105700, "17966"), (201775, "41024"),
            (256225, "58448"), (384350, "103291.75")],
}


@pytest.mark.parametrize("status", IRS_VECTORS)
def test_bracket_data_matches_irs_tables(status):
    for income, expected in IRS_VECTORS[status]:
        assert tax.bracket_tax(D(income), B[status]) == D(expected), (status, income)


def test_single_100k_no_contributions():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "age": 35})
    assert r["taxable_income"] == 83900
    assert r["federal_income_tax"] == 13170  # 5,800 + 22% * (83,900 - 50,400)
    assert r["marginal_rate_pct"] == 22
    assert r["taxable_income_until_next_bracket"] == 21800  # 105,700 - 83,900


def test_traditional_401k_saves_at_marginal_rate():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "age": 35,
                      "traditional_401k_annual": 24500})
    assert r["taxable_income"] == 59400
    assert r["federal_income_tax"] == 7780
    assert r["retirement"]["room_left_401k"] == 0


def test_what_if_max_401k():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "age": 35, "state": "none"})
    s = next(x for x in r["what_if"] if x["scenario"] == "max out traditional 401(k)")
    assert s["extra_pre_tax_contribution"] == 24500
    assert s["federal_tax_saved"] == 5390  # 13,170 - 7,780
    assert s["state_tax_saved"] == 0 and s["net_take_home_cost"] == 24500 - 5390


def test_mfj_200k():
    r = tax.estimate({"filing_status": "mfj", "annual_gross_income": 200000})
    assert r["taxable_income"] == 167800
    assert r["federal_income_tax"] == 26340  # 11,600 + 22% * 67,000


def test_catchup_and_aged_deduction():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 80000, "age": 61})
    assert r["retirement"]["limit_401k"] == 24500 + 11250  # ages 60-63 super catch-up
    assert r["retirement"]["limit_ira"] == 7500 + 1100
    r65 = tax.estimate({"filing_status": "single", "annual_gross_income": 80000, "age": 66})
    assert r65["deduction_used"]["amount"] == 16100 + 2050
    assert r65["retirement"]["limit_401k"] == 24500 + 8000


def test_ira_deduction_phaseout_single_covered():
    base = {"filing_status": "single", "age": 35, "covered_by_workplace_plan": True,
            "traditional_ira_annual": 7500}
    full = tax.estimate(dict(base, annual_gross_income=80000))
    assert full["retirement"]["traditional_ira_deductible_max"] == 7500
    mid = tax.estimate(dict(base, annual_gross_income=86000))  # halfway through 81k-91k
    assert mid["retirement"]["traditional_ira_deductible_max"] == 3750
    none = tax.estimate(dict(base, annual_gross_income=95000))
    assert none["retirement"]["traditional_ira_deductible_max"] == 0
    # non-deductible contribution does not lower AGI
    assert none["adjusted_gross_income"] == 95000


def test_roth_eligibility():
    f = lambda inc: tax.estimate({"filing_status": "single", "annual_gross_income": inc, "age": 30})["retirement"]
    assert f(100000)["roth_ira_eligibility"] == "full"
    assert f(160500)["roth_ira_eligibility"] == "partial"
    assert f(200000)["roth_ira_eligibility"] == "none"


def test_over_limit_warnings_and_itemized():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 150000, "age": 30,
                      "traditional_401k_annual": 30000, "itemized_deductions": 20000})
    assert any("401(k)" in w for w in r["warnings"])
    assert r["deduction_used"] == {"type": "itemized", "amount": 20000}


def test_errors():
    assert "error" in tax.estimate({"filing_status": "single"})
    assert "error" in tax.estimate({"filing_status": "bogus", "annual_gross_income": 1})
    assert "error" in tax.estimate({"filing_status": "single", "annual_gross_income": 1}, year=1999)


def test_zero_taxable_income():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 10000})
    assert r["federal_income_tax"] == 0 and r["marginal_rate_pct"] == 10


def test_integrated_into_planning_and_cli(monkeypatch, tmp_path, capsys):
    plan = planning.build({"filing_status": "single", "annual_gross_income": 100000, "age": 35}, 500.0, 1500.0)
    assert plan["tax"]["federal_income_tax"] == 13170
    assert not any("tax inputs" in m for m in plan["missing_inputs"])
    assert any("tax inputs" in m for m in planning.build({}, 500.0, 1500.0)["missing_inputs"])
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    (tmp_path / "profile.json").write_text(json.dumps(
        {"filing_status": "hoh", "annual_gross_income": 70000}))
    assert cli.main(["tax"]) == 0
    assert json.loads(capsys.readouterr().out)["taxable_income"] == 70000 - 24150


# --- FICA ---

def test_fica_basic_and_wage_base():
    f = tax.fica("single", [D(100000)], 2026)
    assert f["social_security"] == 6200 and f["medicare"] == 1450 and f["additional_medicare"] == 0
    hi = tax.fica("single", [D(250000)], 2026)
    assert hi["social_security"] == D("184500") * D("0.062")  # capped at 11,439
    assert hi["additional_medicare"] == 450  # 0.9% of 50,000 over 200,000
    assert tax.fica("mfj", [D(250000)], 2026)["additional_medicare"] == 0


def test_fica_two_earners_cap_each_but_combined_threshold():
    f = tax.fica("mfj", [D(200000), D(100000)], 2026)
    assert f["social_security"] == D("184500") * D("0.062") + D("6200")
    assert f["additional_medicare"] == D("450")  # 300k - 250k = 50k * 0.9%


def test_401k_does_not_reduce_fica():
    a = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "state": "none"})
    b = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "state": "none",
                      "traditional_401k_annual": 20000})
    assert a["fica"]["total"] == b["fica"]["total"] == 7650
    assert b["federal_income_tax"] < a["federal_income_tax"]


def test_section125_benefits_reduce_fica_and_income_tax():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "state": "none",
                      "pre_tax_benefits_annual": 5000})
    assert r["fica"]["total"] == D("95000") * D("0.0765")
    assert r["taxable_income"] == 95000 - 16100


# --- Arkansas ---

def test_ar_matches_dfa_worked_example():
    # DFA example: net taxable 23,054 -> midrange 23,050 -> 3.4% - 287.97 = 495.73 -> 496; minus 2x$29 = 438
    assert tax.ar_tax(D(23054), 0, 2026) == 496
    assert tax.ar_tax(D(23054), 2, 2026) == 438


@pytest.mark.parametrize("x,expected", [
    (5599, 0),
    (11000, 111),    # mid 10,950: 2% = 219.00 - 111.98 = 107.02 -> actually band 10,901-11,000 mid 10,950
    (50000, 1481),   # mid 49,950: 3.7% = 1848.15 - 367.16
    (94750, 3136),   # mid 94,750: 3.7% - 79.90 - 290 phase-out credit
    (100000, 3620),  # exact: 3.7% - 79.90
    (200000, 7320),  # 7400 - 79.90 = 7320.10
])
def test_ar_bracket_points(x, expected):
    got = tax.ar_tax(D(x), 0, 2026)
    if x == 11000:
        assert got == 107  # 0.02 * 10,950 - 111.98 = 107.02
    else:
        assert got == expected


def test_ar_phaseout_band_is_monotone_with_no_cliff():
    # In 94,701-97,600 the $290 credit shrinks $10 per $100, so tax rises ~13.7 cents per extra dollar there
    xs = [94000, 94750, 96000, 97500, 97650, 97800, 100000]
    vals = [tax.ar_tax(D(x), 0, 2026) for x in xs]
    assert vals == sorted(vals)
    assert vals[3] - vals[2] > 150  # steep inside the phase-out band
    assert vals[5] - vals[4] < 20   # normal 3.7% slope after the band ends (150 * 0.037 = 5.55)


def test_estimate_includes_arkansas_by_default():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "age": 35})
    assert r["state"]["taxable_income"] == 97530  # 100,000 - 2,470
    assert r["state"]["income_tax"] == 3490  # mid 97,550: 3,609.35 - 89.90 = 3,519.45 -> 3,519 - 29 credit
    assert r["total_taxes"] == 13170 + 3490 + 7650
    assert r["estimated_annual_take_home"] == 100000 - r["total_taxes"]
    assert r["combined_marginal_rate_on_next_dollar_pct"] == 22 + 3.7 + 7.65


def test_arkansas_traditional_401k_also_cuts_state_tax():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "age": 35})
    s = next(x for x in r["what_if"] if x["scenario"] == "max out traditional 401(k)")
    assert s["state_tax_saved"] > 800 and s["total_tax_saved"] == s["federal_tax_saved"] + s["state_tax_saved"]
    assert s["net_take_home_cost"] == 24500 - s["total_tax_saved"]


def test_state_none_and_unsupported_state():
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "state": "none"})
    assert r["state"] is None and r["total_taxes"] == 13170 + 7650
    r2 = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "state": "TX"})
    assert r2["state"] is None and any("not modeled" in w for w in r2["warnings"])


def test_ar_mfj_defaults_two_exemptions_and_two_earner_deduction():
    one = tax.estimate({"filing_status": "mfj", "annual_gross_income": 100000})
    two = tax.estimate({"filing_status": "mfj", "annual_gross_income": 100000, "spouse_has_income": True})
    assert one["state"]["personal_credits"] == 58 and one["state"]["standard_deduction"] == 2470
    assert two["state"]["standard_deduction"] == 4940
