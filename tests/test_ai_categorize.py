import json
from datetime import date
from decimal import Decimal

import pytest
from starlette.testclient import TestClient

from fin_client import advisor as adv
from fin_client import ai_categorize as ai
from fin_client import cli, labels, store, web
from fin_client.categorize import DEFAULT_RULES, LABELS_KEY, categorize
from fin_client.merchants import normalize
from fin_client.models import Transaction
from fin_client.paths import REPO_ROOT

H = {"X-Fin-Client": "1"}


def tx(desc, amt, acct="Checking", bank="", d=3):
    return Transaction(date(2026, 9, d), desc, Decimal(amt), acct, bank)


def test_normalize_groups_variants_of_one_merchant():
    keys = {normalize(x) for x in ["KROGER #123 FAYETTEVILLE AR", "KROGER #456 FAYETTEVILLE AR", "KROGER 789 FAYETTEVILLE"]}
    assert keys == {"KROGER FAYETTEVILLE"}
    assert normalize("SQ *BLUE BOTTLE COFFEE 4455 NY") == "BLUE BOTTLE COFFEE"
    assert normalize("TST* JOES PIZZA 0099") == "JOES PIZZA"
    assert normalize("12345 6789") == ""
    assert normalize("AMAZON MKTPLACE PMTS AMZN COM WA") == "AMAZON MKTPLACE PMTS"


def test_label_store_user_beats_ai_and_confidence_gate(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    assert labels.set_label(conn, "JOES PIZZA", "Dining", "ai", 0.9, "m", "JOES PIZZA 12")
    assert labels.set_label(conn, "JOES PIZZA", "Groceries", "user")  # you overwrite the AI
    assert labels.set_label(conn, "JOES PIZZA", "Dining", "ai", 0.99) is False  # AI cannot overwrite you
    assert labels.load_all(conn)["JOES PIZZA"]["category"] == "Groceries"
    assert labels.usable({"source": "ai", "confidence": 0.8}) and not labels.usable({"source": "ai", "confidence": 0.6})
    assert labels.user_examples(conn) == [{"merchant": "JOES PIZZA 12", "category": "Groceries"}] or labels.user_examples(conn)[0]["category"] == "Groceries"
    with pytest.raises(ValueError):
        labels.set_label(conn, "", "x", "user")


def test_categorize_priority_chain():
    lab = {"JOES PIZZA": {"category": "Dining", "source": "ai", "confidence": 0.9},
           "MYSTERY SHOP": {"category": "Gifts & Donations", "source": "ai", "confidence": 0.4},
           "ACME RENTALS": {"category": "Other", "source": "user", "confidence": 1}}
    rules = {**DEFAULT_RULES, LABELS_KEY: lab, "_user": {"Hobbies": ["JOES"]}}
    assert categorize("JOES PIZZA 5", Decimal(-9), rules, "Shopping") == "Hobbies"  # your keyword rules first
    rules2 = {**DEFAULT_RULES, LABELS_KEY: lab}
    assert categorize("JOES PIZZA 5", Decimal(-9), rules2, "Shopping") == "Dining"  # confident AI beats the bank label
    assert categorize("MYSTERY SHOP", Decimal(-9), rules2, "Shopping") == "Shopping"  # unsure AI waits for review
    assert categorize("ACME RENTALS 1", Decimal(-9), rules2, "Housing") == "Other"  # your label beats everything below it


def test_parse_results_is_tolerant_and_never_drops_items():
    batch = [{"example": "A", "key": "A"}, {"example": "B", "key": "B"}, {"example": "C", "key": "C"}]
    txt = 'Sure! ```json\n{"results":[{"id":0,"category":"dining","confidence":0.95},{"id":1,"category":"Spaceships","confidence":0.9}]}\n```'
    r = ai.parse_results(txt, batch)
    assert (r[0]["category"], r[0]["confidence"], r[0]["parsed"]) == ("Dining", 0.95, True)
    assert r[1]["category"] == "Other" and r[1]["confidence"] <= 0.3  # unknown category is downgraded
    assert r[2]["parsed"] is False and r[2]["confidence"] == 0
    assert all(not x["parsed"] for x in ai.parse_results("garbage", batch))
    assert ai.parse_results('[{"id":0,"category":"Income","confidence":2}]', batch[:1])[0]["confidence"] == 1.0


def test_candidates_group_by_merchant_skip_labeled_and_matched_transfers():
    t = [tx("JOES PIZZA 1", "-10"), tx("JOES PIZZA 2", "-20"), tx("PAYROLL ACME", "1000", bank="Income"),
         tx("PAYMENT THANK YOU", "50", "Card", "Transfer (matched)"), tx("GAS STATION 9", "-40", "Card")]
    c = ai.candidates(t, {}, {"Card": "credit card", "Checking": "checking"})
    assert [x["key"] for x in c][:2] == ["PAYROLL ACME", "GAS STATION"] or c[0]["total"] >= c[-1]["total"]
    pizza = next(x for x in c if x["key"] == "JOES PIZZA")
    assert pizza["n"] == 2 and pizza["total"] == 30 and pizza["direction"] == "money out" and pizza["account"] == "checking"
    assert not any(x["key"] == "PAYMENT THANK YOU" for x in c)
    done = ai.candidates(t, {"JOES PIZZA": {"source": "user", "confidence": 1}}, {}, redo_ai=True)
    assert not any(x["key"] == "JOES PIZZA" for x in done)


def fake_complete(mapping):
    def complete(system, messages, model):
        items = json.loads(messages[0]["content"].split("Categorize these merchants:\n")[1])
        return json.dumps({"results": [{"id": i["id"], "category": mapping.get(i["merchant"].split()[0], "Other"), "confidence": 0.9 if i["merchant"].split()[0] in mapping else 0.3}
                                       for i in items]})
    return complete


def test_run_saves_labels_with_confidence_and_uses_your_examples(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    labels.set_label(conn, "OLD SHOP", "Shopping", "user", example="OLD SHOP 1")
    seen = []

    def complete(system, messages, model):
        seen.append(messages[0]["content"])
        return fake_complete({"JOES": "Dining"})(system, messages, model)

    cands = ai.candidates([tx("JOES PIZZA 1", "-10"), tx("ZZZ UNKNOWN 2", "-5")], {}, {})
    res = ai.run(conn, cands, "local", "m", complete=complete)
    assert res == {"merchants": 2, "saved": 2, "applied_automatically": 1, "needs_review": 1, "unparsed": 0}
    got = labels.load_all(conn)
    assert got["JOES PIZZA"]["category"] == "Dining" and got["JOES PIZZA"]["source"] == "ai" and got["JOES PIZZA"]["model"] == "m"
    assert "OLD SHOP" in seen[0] and "Shopping" in seen[0]  # your labels are few-shot examples


def test_evaluate_scores_ai_against_your_labels_and_the_rules_baseline(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    names = [f"MERCH{c} STORE" for c in "ABCDEFGHIJKL"]
    t = [tx(n + " 1", "-10", bank="Shopping") for n in names]
    for i, n in enumerate(names):
        labels.set_label(conn, normalize(n), "Dining" if i < 9 else "Groceries", "user", example=n + " 1")
    truth_map = {n.split()[0]: "Dining" for n in names}  # AI says Dining for everything: 9/12 right
    res = ai.evaluate(conn, t, DEFAULT_RULES, {}, "local", "m", complete=fake_complete(truth_map))
    assert res["n"] == 12 and res["ai_accuracy"] == 75.0
    assert res["rules_only_accuracy"] == 0.0  # the bank said Shopping for all of them
    assert len(res["mistakes"]) == 3 and res["confident_accuracy"] == 75.0
    few = ai.evaluate(store.connect(tmp_path / "e.db"), t, DEFAULT_RULES, {}, "local")
    assert "error" in few


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    c = TestClient(web.create_app(), base_url="http://127.0.0.1:8780")
    c.post("/api/import?filename=synthetic_history.csv&account=Main&type=checking", headers=H,
           content=(REPO_ROOT / "samples" / "synthetic_history.csv").read_bytes())
    return c


def test_api_status_review_and_accept_flow(client, monkeypatch):
    monkeypatch.setattr(ai, "ollama_json", lambda s, m, model: json.dumps({"results": [
        {"id": i, "category": "Shopping", "confidence": 0.6} for i in range(15)]}))
    st = client.get("/api/categorize/status").json()
    assert st["unlabeled"] > 5 and st["yours"] == 0
    lines = [json.loads(x) for x in client.post("/api/categorize/run", headers=H, json={"engine": "local"}).text.splitlines()]
    assert lines[-1]["result"]["saved"] > 0 and lines[0]["total"] >= 1
    rv = client.get("/api/labels/review").json()
    assert rv["review"] and rv["review"][0]["source"] == "ai" and "Shopping" in rv["taxonomy"]
    key = rv["review"][0]["key"]
    assert client.post("/api/labels", headers=H, json={"key": key, "category": "dining", "merchant": "x"}).status_code == 200
    after = client.get("/api/labels/review").json()
    assert any(r["key"] == key and r["category"] == "Dining" for r in after["yours"])
    assert all(r["key"] != key for r in after["review"])
    assert client.delete(f"/api/labels?key={key}", headers=H).status_code == 200
    assert client.post("/api/labels", headers=H, json={"key": "", "category": "x"}).status_code == 400


def test_claude_categorization_needs_the_previewed_digest_and_sends_only_merchant_text(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    sent = []
    monkeypatch.setattr(adv, "claude_stream", lambda p: sent.append(p) or iter([json.dumps({"results": []})]))
    assert client.post("/api/categorize/run", headers=H, json={"engine": "claude"}).status_code == 409
    prev = client.post("/api/categorize/preview", headers=H, json={"engine": "claude"}).json()
    assert prev["digest"] and prev["requests"] >= 1 and "Categorize these merchants" in prev["text"]
    assert "Main" not in prev["text"] and "2500" not in prev["text"].replace("2500.0", "")  # no account names; no payroll totals
    r = client.post("/api/categorize/run", headers=H, json={"engine": "claude", "digest": prev["digest"]})
    assert r.status_code == 200 and sent and sent[0]["messages"][0]["content"] in prev["text"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert client.post("/api/categorize/run", headers=H, json={"engine": "claude", "digest": prev["digest"]}).status_code == 400


def test_cli_categorize_local_and_claude_gate(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    cli.main(["import", str(REPO_ROOT / "samples" / "synthetic_history.csv")])
    capsys.readouterr()
    monkeypatch.setattr(ai, "ollama_json", lambda s, m, model: json.dumps({"results": [{"id": i, "category": "Dining", "confidence": 0.9} for i in range(15)]}))
    assert cli.main(["categorize"]) == 0
    assert "labels saved" in capsys.readouterr().out
    assert cli.main(["categorize"]) == 0 and "already has a label" in capsys.readouterr().out
    assert cli.main(["categorize", "--eval"]) == 1  # no labels of yours yet
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["categorize", "--engine", "claude", "--redo"]) == 1
    assert "credential" in capsys.readouterr().err


def test_normalize_drops_order_ids_and_bank_noise():
    assert normalize("AMAZON MKTPL*5L2110NU1 DDA PIN POS PUR 0912") == "AMAZON MKTPL"
    assert normalize("Prime Video *YG6RE68Z3 DDA PIN POS PUR") == "PRIME VIDEO" == normalize("Prime Video *5X95W0VP2")
    assert normalize("ROCKLAHOMA* ROCKLAHOMA DDA PIN POS PUR") == "ROCKLAHOMA"
    assert normalize("SHELL SERVICE STATION DDA PIN POS PUR CD8276 FAYETTEVILLE AR") == "SHELL SERVICE STATION"
    assert normalize("WALMART.COM # PIN POS RTN TO DDA") == "WALMART"


def test_refunds_offset_their_category_instead_of_counting_as_income():
    from fin_client import analytics
    t = [tx("PAYROLL", "1000", bank="Income"), tx("WALMART SUPERCENTER", "-50", bank="Shopping"),
         tx("WALMART SUPERCENTER RTN", "10", bank="Shopping", d=5)]
    m = analytics.cashflow(t, {**DEFAULT_RULES})[0]
    assert m.income == 1000 and m.spending == 40 and m.by_category["Shopping"] == 40
