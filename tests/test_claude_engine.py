import json

import pytest
from starlette.testclient import TestClient

from fin_client import advisor as adv
from fin_client import cli, snapshot, web
from fin_client.categorize import DEFAULT_RULES
from fin_client.importers import import_file
from fin_client.paths import REPO_ROOT

SAMPLES = REPO_ROOT / "samples"
H = {"X-Fin-Client": "1"}
PROFILE = {"age": 30, "debts": [{"name": "Capital One Savor ...1596", "balance": 1415.92, "apr_pct": 0, "min_payment": 50},
                                {"name": "Discover it ...3583", "balance": 276.35, "apr_pct": 22, "min_payment": 35}]}


def snap():
    return snapshot.build(import_file(SAMPLES / "synthetic_history.csv"), DEFAULT_RULES, PROFILE)


def test_redaction_hides_account_and_merchant_names_but_keeps_numbers():
    safe = json.dumps(adv.redact_snapshot(snap()))
    for secret in ("Savor", "1596", "Discover", "3583", "EXAMPLE APARTMENTS RENT", "ACME CORP PAYROLL"):
        assert secret not in safe
    assert "Debt 1" in safe and "Debt 2" in safe and "Recurring item 1" in safe
    assert "1415.92" in safe and "2500" in safe  # the figures the advice needs survive
    shown = json.dumps(adv.redact_snapshot(snap(), include_merchants=True))
    assert "EXAMPLE APARTMENTS RENT" in shown and "Savor" not in shown  # merchants opt-in; account names never


def test_payload_is_exact_deterministic_and_never_has_raw_rows():
    p1 = adv.build_claude_payload(snap(), [], "What now?")
    p2 = adv.build_claude_payload(snap(), [], "What now?")
    assert adv.payload_digest(p1) == adv.payload_digest(p2)
    assert adv.payload_digest(p1) != adv.payload_digest(adv.build_claude_payload(snap(), [], "Other question"))
    assert p1["model"] == "claude-opus-5-5" and p1["messages"] == [{"role": "user", "content": "What now?"}]
    text = adv.render_payload(p1)
    assert "MYSTERY VENDOR" not in text and "SAMPLE GROCERY MART" not in text and "What now?" in text
    hist = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "system", "content": "x"}]
    assert [m["role"] for m in adv.build_claude_payload(snap(), hist, "q")["messages"]] == ["user", "assistant", "user"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    c = TestClient(web.create_app(), base_url="http://127.0.0.1:8780")
    c.post("/api/import?filename=synthetic_history.csv&account=Main", headers=H,
           content=(SAMPLES / "synthetic_history.csv").read_bytes())
    return c


def test_default_engine_is_local_and_claude_is_never_called_without_approval(client, monkeypatch):
    called = []
    monkeypatch.setattr(adv, "claude_stream", lambda p: called.append(p) or iter(["x"]))
    monkeypatch.setattr(web, "Advisor", lambda s, m: type("B", (), {"messages": [], "ask": lambda self, q: iter(["local"])})())
    assert client.post("/api/advise", headers=H, json={"question": "hi"}).text == "local"
    r = client.post("/api/advise", headers=H, json={"question": "hi", "engine": "claude"})  # no digest
    assert r.status_code == 409 and called == []


def test_preview_then_approved_send(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    sent = []
    monkeypatch.setattr(adv, "claude_stream", lambda p: sent.append(p) or iter(["Hel", "lo"]))
    prev = client.post("/api/advise/preview", headers=H, json={"question": "hi"}).json()
    assert prev["ready"] and prev["digest"] and "hi" in prev["text"] and prev["approx_input_tokens"] > 100
    assert "test-key-not-real" not in json.dumps(prev)
    r = client.post("/api/advise", headers=H, json={"question": "hi", "engine": "claude", "digest": prev["digest"]})
    assert r.status_code == 200 and r.text == "Hello"
    assert sent == [prev["payload"]]  # what was sent is exactly what was previewed


def test_changed_question_or_merchant_toggle_invalidates_approval(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(adv, "claude_stream", lambda p: iter(["x"]))
    prev = client.post("/api/advise/preview", headers=H, json={"question": "hi"}).json()
    r = client.post("/api/advise", headers=H, json={"question": "different", "engine": "claude", "digest": prev["digest"]})
    assert r.status_code == 409
    r = client.post("/api/advise", headers=H, json={"question": "hi", "engine": "claude", "digest": prev["digest"], "include_merchants": True})
    assert r.status_code == 409


def test_claude_requires_credential_and_reports_status(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert client.get("/api/claude").json()["ready"] is False
    prev = client.post("/api/advise/preview", headers=H, json={"question": "hi"}).json()
    r = client.post("/api/advise", headers=H, json={"question": "hi", "engine": "claude", "digest": prev["digest"]})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["error"]


def test_claude_failure_is_reported_without_crashing(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def boom(p):
        raise ConnectionError("down")
        yield

    monkeypatch.setattr(adv, "claude_stream", boom)
    prev = client.post("/api/advise/preview", headers=H, json={"question": "hi"}).json()
    r = client.post("/api/advise", headers=H, json={"question": "hi", "engine": "claude", "digest": prev["digest"]})
    assert "Claude request failed: ConnectionError" in r.text


def test_cli_requires_explicit_yes_every_turn(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    sent = []
    monkeypatch.setattr(adv, "claude_stream", lambda p: sent.append(p) or iter(["ok"]))
    answers = iter(["first question", "n",              # shown in full, declined
                    "first question", "", "y",          # skip the view prompt, approve
                    "second", "", "", "exit"])          # follow-up: skip view, Enter = no
    out = []
    rc = cli.claude_chat(snap(), False, ask=lambda _p: next(answers), say=out.append)
    assert rc == 0 and len(sent) == 1 and sent[0]["messages"][-1]["content"] == "first question"
    shown = "\n".join(out)
    assert "EXACTLY WHAT WILL BE SENT" in shown and "Not sent." in shown and "Savor" not in shown


def test_cli_refuses_without_credential(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert cli.claude_chat(snap(), False, ask=lambda _p: "x", say=lambda s: None) == 1
    assert "credential" in capsys.readouterr().err
