from datetime import date

import pytest
from starlette.testclient import TestClient

from fin_client import goals, store, web
from fin_client.paths import REPO_ROOT

TODAY = date(2026, 10, 17)  # plan starts 2026-11


def acct(name, type_, bal, apr=None, mn=None):
    return {"name": name, "type": type_, "kind": "liability" if type_ in ("credit_card", "loan") else "asset",
            "balance": bal, "apr_pct": apr, "min_payment": mn}


def goal(kind, name="g", **kw):
    return {"id": kw.pop("id", 1), "kind": kind, "kind_label": goals.KINDS[kind], "name": name, "target": kw.get("target"),
            "target_date": kw.get("target_date"), "current": kw.get("current"), "months": kw.get("months"),
            "monthly": kw.get("monthly"), "priority": kw.get("priority", 50), "notes": ""}


def test_waterfall_order_and_dates_without_debt():
    # essentials 1000, surplus 500, no cash: starter fund (2 mo) -> 3-month fund (4 mo) -> savings goal 3000 (6 mo)
    plan = goals.build_plan([goal("savings", "Car", target=3000, current=0)], [], 1000, 500, TODAY)
    assert plan["this_month"] == [{"bucket": "Starter emergency fund", "amount": 500.0}]
    ms = {m["label"]: m["date"] for m in plan["milestones"]}
    assert ms["Starter emergency fund reached"] == "2026-12"
    assert ms["Emergency fund (3 months) reached"] == "2027-04"
    assert ms["Car reached"] == "2027-10"
    assert plan["goals"][0]["eta"] == "2027-10" and plan["goals"][0]["status"] == "on track"


def test_high_apr_debt_gets_extra_before_the_emergency_fund_top_up():
    accts = [acct("Checking", "checking", 2000), acct("Card", "credit_card", 1000, apr=20, mn=50)]
    plan = goals.build_plan([], accts, 1000, 300, TODAY)
    assert plan["this_month"][0] == {"bucket": "Extra to Card", "amount": 300.0}  # starter fund already covered by cash
    assert plan["debt_free"] is not None and plan["debt_free"] < "2027-03"
    assert any("Card paid off" in m["label"] for m in plan["milestones"])
    assert plan["debts"][0]["paid_off"] == plan["debt_free"]


def test_zero_percent_plan_debt_waits_behind_emergency_fund_and_freed_minimum_rolls_forward():
    accts = [acct("Plan card", "credit_card", 300, apr=0, mn=100), acct("Savings", "savings", 0)]
    plan = goals.build_plan([goal("savings", "Trip", target=1000, current=0)], accts, 500, 200, TODAY)
    assert plan["this_month"][0]["bucket"] == "Starter emergency fund"  # 0% APR is not "high APR"
    paid = next(m for m in plan["milestones"] if "Plan card paid off" in m["label"])
    assert paid["date"] == "2027-01"  # minimums alone clear 300 in 3 months
    trip = next(g for g in plan["goals"] if g["name"] == "Trip")
    assert trip["eta"] is not None


def test_dated_goal_status_and_required_monthly():
    behind = goals.build_plan([goal("savings", "Deposit", target=1200, current=0, target_date="2027-01-31")], [], 0, 100, TODAY)
    g = behind["goals"][0]
    assert g["status"] == "behind" and g["required_monthly"] == 400.0 and g["months_late"] >= 1  # 1200 / 3 months
    ontrack = goals.build_plan([goal("savings", "Deposit", target=1200, current=0, target_date="2027-12-31")], [], 0, 400, TODAY)
    assert ontrack["goals"][0]["status"] == "on track"


def test_no_surplus_and_missing_inputs_are_flagged_not_guessed():
    plan = goals.build_plan([], [acct("Card", "credit_card", 500)], 800, -50, TODAY)
    assert any("no surplus" in w for w in plan["warnings"])
    assert any("No cash balances" in w for w in plan["warnings"])
    text = " ".join(plan["assumptions"])
    assert "APR unknown" in text and "minimum payment unknown" in text
    assert plan["this_month"] == []


def test_debt_free_goal_and_investing_remainder():
    accts = [acct("Loan", "loan", 600, apr=10, mn=100), acct("Checking", "checking", 5000)]
    gs = [goal("debt_free", "No debt", id=1, target_date="2027-12-31"), goal("invest", "Retirement", id=2, monthly=150)]
    plan = goals.build_plan(gs, accts, 1000, 1000, TODAY)
    d = next(g for g in plan["goals"] if g["kind"] == "debt_free")
    assert d["status"] == "on track" and d["eta"] == "2026-11"
    later = [t for t in plan["timeline"] if t["invested"] > 0]
    assert later and later[0]["debt"] == 0
    none = goals.build_plan([goal("debt_free", "x")], [acct("Checking", "checking", 1)], 100, 100, TODAY)
    assert none["goals"][0]["status"] == "no debts to pay off"


def test_validation_and_storage(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    with pytest.raises(ValueError):
        goals.add_goal(conn, "bogus", "x")
    with pytest.raises(ValueError):
        goals.add_goal(conn, "savings", "no target")
    with pytest.raises(ValueError):
        goals.add_goal(conn, "savings", "x", target=100, target_date="soon")
    gid = goals.add_goal(conn, "savings", "Car", target="$8,000", target_date="2027-09-30", current=500, priority=10)
    got = goals.list_goals(conn)
    assert got[0]["id"] == gid and got[0]["target"] == 8000.0 and got[0]["kind_label"] == "Savings goal"
    goals.delete_goal(conn, gid)
    assert goals.list_goals(conn) == []


SAMPLES = REPO_ROOT / "samples"
H = {"X-Fin-Client": "1"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    c = TestClient(web.create_app(), base_url="http://127.0.0.1:8780")
    c.post("/api/import?filename=synthetic_history.csv&account=Main&type=checking", headers=H,
           content=(SAMPLES / "synthetic_history.csv").read_bytes())
    return c


def test_goals_api_roundtrip_and_snapshot_integration(client, monkeypatch):
    client.post("/api/accounts", headers=H, json={"name": "Main", "type": "checking", "balance": 1200})
    r = client.post("/api/goals", headers=H, json={"kind": "savings", "name": "Car", "target": 6000, "target_date": "2028-06-30"})
    assert r.status_code == 200
    d = r.json()
    assert d["goals"][0]["name"] == "Car" and d["plan"]["goals"][0]["name"] == "Car"
    assert d["plan"]["timeline"] and d["plan"]["this_month"]
    assert client.post("/api/goals", headers=H, json={"kind": "savings", "name": "bad"}).status_code == 400
    snap = client.get("/api/overview").json()["snapshot"]
    assert snap["goals_plan"]["goals"][0]["name"] == "Car" and "timeline" not in snap["goals_plan"]  # trimmed for the advisor
    gid = d["goals"][0]["id"]
    assert client.delete(f"/api/goals?id={gid}", headers=H).json()["goals"] == []
    assert client.delete("/api/goals?id=abc", headers=H).status_code == 400
    assert client.delete(f"/api/goals?id={gid}").status_code == 403  # writes need the custom header


def test_already_funded_emergency_goal_reports_reached():
    accts = [acct("Savings", "savings", 5000)]
    plan = goals.build_plan([goal("emergency_fund", "EF", months=3)], accts, 1000, 400, TODAY)
    g = plan["goals"][0]
    assert g["status"] == "on track" and g["eta"] == "2026-11" and g["current"] == 5000
    assert plan["this_month"][0]["bucket"] != "Starter emergency fund"
