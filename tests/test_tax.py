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
    r = tax.estimate({"filing_status": "single", "annual_gross_income": 100000, "age": 35})
    s = next(x for x in r["what_if"] if x["scenario"] == "max out traditional 401(k)")
    assert s["extra_pre_tax_contribution"] == 24500
    assert s["federal_tax_saved"] == 5390  # 13,170 - 7,780
    assert s["net_take_home_cost"] == 24500 - 5390


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
