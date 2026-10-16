import json
from decimal import Decimal

import pytest

from fin_client import cli, planning
from fin_client.paths import REPO_ROOT

HISTORY = REPO_ROOT / "samples" / "synthetic_history.csv"


def test_single_debt_known_answer():
    # $1000 at 12% APR, $100/mo: closed form says ~10.6 -> 11 months
    r = planning.debt_plan([{"name": "card", "balance": 1000, "apr_pct": 12, "min_payment": 100}], None)
    s = r["scenarios"][0]
    assert s["months_to_debt_free"] == 11
    assert 55 < s["total_interest"] < 66
    assert s["payoff_order"] == ["card"]


def test_avalanche_beats_snowball_on_interest():
    debts = [{"name": "big", "balance": 5000, "apr_pct": 25, "min_payment": 100},
             {"name": "small", "balance": 500, "apr_pct": 5, "min_payment": 25}]
    r = planning.debt_plan(debts, 400)
    by = {s["scenario"]: s for s in r["scenarios"]}
    av, sn = by["avalanche, 100% of surplus extra"], by["snowball, 100% of surplus extra"]
    assert av["total_interest"] < sn["total_interest"]
    assert av["months_to_debt_free"] <= sn["months_to_debt_free"]
    assert by["minimums only"]["months_to_debt_free"] > av["months_to_debt_free"]
    assert r["high_apr_debts"] == ["big"]


def test_payment_below_interest_never_pays_off():
    r = planning.debt_plan([{"name": "x", "balance": 10000, "apr_pct": 30, "min_payment": 100}], None)
    s = r["scenarios"][0]
    assert s["months_to_debt_free"] is None and s["note"]


def test_missing_min_payment_is_flagged():
    r = planning.debt_plan([{"name": "x", "balance": 1000, "apr_pct": 10}], None)
    assert r["assumptions"] and "assumed" in r["assumptions"][0]


def test_emergency_fund():
    e = planning.emergency_fund(1000, 1500, 500)
    assert e["target_3_months"] == 3000 and e["target_6_months"] == 6000
    assert e["months_covered"] == 1.5
    assert e["shortfall_to_3_months"] == 1500 and e["months_to_3_at_contribution"] == 3
    assert "cash_savings" not in planning.emergency_fund(1000, None, None)


def test_retirement_zero_growth_is_arithmetic():
    p = {"age": 30, "retire_age": 40, "retirement_balance": 1000, "monthly_retirement_contribution": 100,
         "expected_return_pct": 0, "inflation_pct": 0}
    base = next(s for s in planning.retirement(p)["scenarios"] if s["scenario"] == "base")
    assert base["projected_balance"] == 1000 + 100 * 120
    assert base["sustainable_monthly_income"] == round(13000 * 0.04 / 12)


def test_retirement_scenarios_ordered_and_target():
    p = {"age": 30, "retire_age": 65, "retirement_balance": 20000, "monthly_retirement_contribution": 500,
         "target_retirement_spending_monthly": 4000}
    sc = {s["scenario"]: s for s in planning.retirement(p)["scenarios"]}
    assert sc["conservative"]["projected_balance"] < sc["base"]["projected_balance"] < sc["optimistic"]["projected_balance"]
    assert sc["base"]["balance_needed"] == 1_200_000
    # contributing exactly the required amount should land on target
    p2 = dict(p, monthly_retirement_contribution=sc["base"]["monthly_contribution_needed"])
    assert planning.retirement(p2)["scenarios"][1]["projected_balance"] == pytest.approx(1_200_000, rel=0.002)


def test_retirement_bad_ages():
    assert "error" in planning.retirement({"age": 65, "retire_age": 60})


def test_build_reports_missing_inputs():
    plan = planning.build({}, 1000.0, 1500.0)
    assert "retirement" not in plan and "debt_payoff" not in plan
    assert any("cash_savings" in m for m in plan["missing_inputs"])
    assert plan["emergency_fund"]["target_3_months"] == 4500.0


def test_cli_plan_and_snapshot_integration(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    (tmp_path / "profile.json").write_text(json.dumps({
        "age": 30, "retire_age": 65, "cash_savings": 2000,
        "debts": [{"name": "card", "balance": 3000, "apr_pct": 24, "min_payment": 90}]}))
    cli.main(["import", str(HISTORY)])
    capsys.readouterr()
    assert cli.main(["plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["debt_payoff"]["total_debt"] == 3000
    assert len(plan["debt_payoff"]["scenarios"]) == 4  # surplus is positive
    assert plan["retirement"]["years_to_retirement"] == 35
    cli.main(["advise", "--show-context"])
    assert "planning" in json.loads(capsys.readouterr().out)
