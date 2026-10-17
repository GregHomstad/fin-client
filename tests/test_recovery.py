from datetime import date
from decimal import Decimal

from fin_client import analytics, recovery, snapshot, store
from fin_client.categorize import DEFAULT_RULES
from fin_client.models import Transaction

TODAY = date(2026, 10, 17)


def acct(name, type_, bal, apr=None, mn=None, ear=None, ear_for=""):
    return {"name": name, "type": type_, "kind": "liability" if type_ in ("credit_card", "loan") else "asset", "balance": bal,
            "apr_pct": apr, "min_payment": mn, "earmarked": ear, "earmark_for": ear_for}


def test_position_stress_test_and_trims_use_exact_arithmetic():
    accts = [acct("Chk", "checking", 1000), acct("Sav", "savings", 3000), acct("Card", "credit_card", 500, 20, 50)]
    rc = recovery.build(300, 1000, {"Dining": 200, "Shopping": 100, "Housing": 900}, accts, [], {}, TODAY)
    p = rc["position"]
    assert p["usable_cash"] == 4000 and p["must_pay_per_month"] == 1050 and p["months_covered_today"] == 3.8
    assert p["debt_not_yet_funded"] == 500 and p["net_worth"] == 3500
    stress = {r["months_without_income"]: r for r in rc["income_stops"]}
    assert stress[1]["shortfall_today"] == 0 and stress[6]["cash_needed"] == 6300 and stress[6]["shortfall_today"] == 2300
    d = rc["discretionary"]
    assert d["per_month"] == {"Dining": 200.0, "Shopping": 100.0} and d["total_per_month"] == 300  # Housing is not discretionary
    t10 = d["scenarios"][0]
    assert t10["trim_pct"] == 10 and t10["monthly_freed"] == 30 and t10["new_monthly_surplus"] == 330
    assert all(t["months_sooner"] is None or t["months_sooner"] >= 0 for t in d["scenarios"])


def test_pending_deposits_and_money_set_aside_change_the_cushion_not_the_debts():
    accts = [acct("Chk", "checking", 800), acct("Sav", "savings", 9000, ear=7000, ear_for="Dad"), acct("Dad", "loan", 7000, 0)]
    rc = recovery.build(0, 1000, {}, accts, [], {"pending_deposits": [{"amount": 1500}]}, TODAY)
    p = rc["position"]
    assert p["usable_cash"] == 2800 and p["debt_not_yet_funded"] == 0 and p["set_aside_for_debts"] == 7000
    assert p["pending_deposits"] == 1500 and p["months_covered_after_deposits"] == 4.3
    assert rc["income_stops"][3]["shortfall_today"] == 3200 and rc["income_stops"][3]["shortfall_after_deposits"] == 1700


def test_unknown_minimums_are_flagged_not_guessed():
    rc = recovery.build(100, 500, {}, [acct("Chk", "checking", 500), acct("Card", "credit_card", 300, 25)], [], {}, TODAY)
    assert rc["position"]["debt_minimums_per_month"] == 0 and any("Minimum payment unknown for: Card" in n for n in rc["notes"])


def month(m, inc, sp):
    mo = analytics.Month(m)
    mo.income, mo.spending = Decimal(inc), Decimal(sp)
    return mo


def test_twelve_month_window_marks_missing_months_and_ends_at_the_latest_data():
    w = analytics.window([month("2026-08", 100, 50), month("2026-10", 200, 80)], 12)
    assert len(w) == 12 and w[0]["month"] == "2025-11" and w[-1]["month"] == "2026-10"
    assert w[-1]["income"] == 200 and not w[-1]["missing"]
    assert next(x for x in w if x["month"] == "2026-09")["missing"] is True
    assert analytics.window([], 12) == []
    assert [x["month"] for x in analytics.window([month("2026-01", 1, 1)], 3)] == ["2025-11", "2025-12", "2026-01"]


def test_snapshot_carries_window_and_recovery(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    t = [Transaction(date(2026, m, 5), "PAYROLL", Decimal("2000"), "Chk", "Income") for m in (7, 8, 9, 10)] + \
        [Transaction(date(2026, m, 6), "RENT APARTMENTS", Decimal("-900"), "Chk") for m in (7, 8, 9, 10)]
    store.add(conn, t)
    snap = snapshot.build(t, DEFAULT_RULES, {}, conn)
    assert len(snap["history_window"]) == 12 and snap["months_with_data"] == 4
    assert snap["recovery"]["position"]["monthly_surplus"] == 1100
