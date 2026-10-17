import json

import pytest
from starlette.testclient import TestClient

from fin_client import web
from fin_client.paths import REPO_ROOT

SAMPLES = REPO_ROOT / "samples"
H = {"X-Fin-Client": "1"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    return TestClient(web.create_app(), base_url="http://127.0.0.1:8780")


def upload(client, name, account="", type_=""):
    return client.post(f"/api/import?filename={name}&account={account}&type={type_}", headers=H,
                       content=(SAMPLES / name).read_bytes())


def test_empty_overview(client):
    r = client.get("/api/overview").json()
    assert r["has_data"] is False and r["snapshot"] is None and r["accounts"] == []


def test_import_overview_and_security_headers(client):
    r = upload(client, "synthetic_history.csv", "Main Checking", "checking")
    assert r.status_code == 200 and r.json()["added"] == 37
    o = client.get("/api/overview")
    assert o.headers["cache-control"] == "no-store" and "default-src 'self'" in o.headers["content-security-policy"]
    d = o.json()
    assert d["has_data"] and len(d["months"]) == 5 and d["snapshot"]["monthly_averages"]["income"] == 2500
    assert d["accounts"][0]["name"] == "Main Checking" and d["accounts"][0]["type"] == "checking"
    assert any(r["merchant"] == "EXAMPLE APARTMENTS RENT" for r in d["recurring"])
    assert d["uncategorized"]["merchants"] >= 1


def test_account_balances_flow_into_net_worth_and_plan(client):
    upload(client, "synthetic_history.csv", "Main Checking", "checking")
    assert client.post("/api/accounts", headers=H, json={"name": "Main Checking", "type": "checking", "balance": 1500}).status_code == 200
    r = client.post("/api/accounts", headers=H, json={"name": "Visa", "type": "credit_card", "balance": 2000,
                                                       "apr_pct": 22, "min_payment": 60}).json()
    assert r["net_worth"]["net_worth"] == -500
    plan = client.get("/api/overview").json()["snapshot"]["planning"]
    assert plan["debt_payoff"]["total_debt"] == 2000 and plan["emergency_fund"]["cash_savings"] == 1500
    assert client.post("/api/accounts", headers=H, json={"name": "x", "type": "nope"}).status_code == 400


def test_transactions_filter_and_rules(client):
    upload(client, "synthetic_history.csv", "Main")
    t = client.get("/api/transactions?q=mystery").json()
    assert [x["description"] for x in t["transactions"]] == ["MYSTERY VENDOR"]
    assert t["transactions"][0]["category"] == "Uncategorized"
    assert client.post("/api/rules", headers=H, json={"keyword": "MYSTERY", "category": "Hobbies"}).json() == {"saved": True}
    assert client.get("/api/transactions?q=mystery").json()["transactions"][0]["category"] == "Hobbies"
    assert client.get("/api/transactions?month=2026-06&limit=3").json()["transactions"].__len__() == 3
    assert client.post("/api/rules", headers=H, json={"keyword": "", "category": "x"}).status_code == 400


def test_profile_roundtrip_and_tax(client):
    upload(client, "synthetic_history.csv", "Main")
    client.put("/api/profile", headers=H, json={"filing_status": "single", "annual_gross_income": 100000, "age": 35, "note": ""})
    p = client.get("/api/profile").json()
    assert p["profile"] == {"filing_status": "single", "annual_gross_income": 100000, "age": 35}
    assert client.get("/api/overview").json()["snapshot"]["planning"]["tax"]["federal_income_tax"] == 13170


def test_import_rejects_bad_files(client):
    assert client.post("/api/import?filename=x.exe", headers=H, content=b"abc").status_code == 400
    assert client.post("/api/import?filename=x.csv", headers=H, content=b"").status_code == 400
    r = client.post("/api/import?filename=x.csv", headers=H, content=b"foo,bar\n1,2\n")
    assert r.status_code == 400 and "bank export" in r.json()["error"]


def test_report_pdf(client):
    assert client.get("/api/report.pdf").status_code == 400  # nothing imported
    upload(client, "synthetic_history.csv", "Main")
    r = client.get("/api/report.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_guard_rejects_foreign_host_and_cross_site_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    evil = TestClient(web.create_app(), base_url="http://evil.example")
    assert evil.get("/api/overview").status_code == 403  # DNS rebinding
    ok = TestClient(web.create_app(), base_url="http://localhost:8780")
    assert ok.get("/api/overview").status_code == 200
    assert ok.post("/api/rules", json={"keyword": "a", "category": "b"}).status_code == 403  # no custom header
    r = ok.post("/api/rules", headers={**H, "Origin": "http://evil.example"}, json={"keyword": "a", "category": "b"})
    assert r.status_code == 403
    r = ok.post("/api/rules", headers={**H, "Origin": "http://localhost:8780"}, json={"keyword": "a", "category": "b"})
    assert r.status_code == 200


def test_advise_streams_from_stub(client, monkeypatch):
    upload(client, "synthetic_history.csv", "Main")
    monkeypatch.setattr(web, "Advisor", lambda snap, model: type("B", (), {
        "messages": [], "ask": lambda self, q: iter(["Hel", "lo ", q])})())
    r = client.post("/api/advise", headers=H, json={"question": "hi"})
    assert r.text == "Hello hi"
    monkeypatch.setenv("OLLAMA_HOST", "http://evil.example")
    assert client.post("/api/advise", headers=H, json={"question": "hi"}).status_code == 400


def test_static_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "stylesheet" not in r.text or "http" not in r.text.split("stylesheet")[1][:80]
    assert json.dumps(client.get("/api/ollama").json()["running"]) in ("true", "false")


def test_multi_account_export_matches_card_payment(client):
    r = upload(client, "synthetic_multi.csv", type_="credit_card")  # rows carry their own Account column
    assert r.json()["added"] == 8
    d = client.get("/api/overview").json()
    assert {a["name"] for a in d["accounts"]} == {"DEMO CHECKING", "DEMO VISA CARD"}
    t = client.get("/api/transactions?account=DEMO%20VISA%20CARD").json()["transactions"]
    pay = next(x for x in t if x["description"] == "PAYMENT THANK YOU")
    assert pay["internal"] and pay["category"] == "Transfers"
    sep = next(m for m in d["months"] if m["month"] == "2026-09")
    assert sep["income"] == 0 and round(sep["spending"], 2) == 392.75  # purchases only; the payment is not spending
