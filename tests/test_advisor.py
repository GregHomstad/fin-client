import json

import pytest

from fin_client import advisor, cli, snapshot
from fin_client.categorize import DEFAULT_RULES
from fin_client.importers import import_file
from fin_client.paths import REPO_ROOT

HISTORY = REPO_ROOT / "samples" / "synthetic_history.csv"


def test_snapshot_has_aggregates_not_rows():
    snap = snapshot.build(import_file(HISTORY), DEFAULT_RULES, {"age": 30})
    blob = json.dumps(snap)
    assert snap["monthly_averages"]["income"] == 2500.0
    assert snap["monthly_averages"]["savings_rate_pct"] == pytest.approx(44.55, abs=0.05)
    assert snap["average_spending_by_category"]["Housing"] == 1100.0
    assert snap["user_profile"] == {"age": 30}
    assert "MYSTERY VENDOR" not in blob and "DEMO PHARMACY" not in blob  # one-offs never leak
    assert "SAMPLE GROCERY MART" not in blob
    assert "EXAMPLE APARTMENTS RENT" in blob  # recurring commitments are named
    assert "date" not in {k for r in snap["recurring_items"] for k in r}


def test_snapshot_empty():
    assert "error" in snapshot.build([])


def test_advisor_keeps_history_and_grounds_prompt():
    seen = []

    def fake(messages, model):
        seen.append((list(messages), model))
        yield "Hello "
        yield "there"

    bot = advisor.Advisor({"monthly_averages": {"income": 2500}}, "m", fake)
    assert "".join(bot.ask("hi")) == "Hello there"
    list(bot.ask("again"))
    msgs, model = seen[1]
    assert model == "m"
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert '"income": 2500' in msgs[0]["content"]


def test_ollama_url_rejects_remote(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://example.com:11434")
    with pytest.raises(RuntimeError):
        advisor.ollama_url()
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    assert advisor.ollama_url() == "http://127.0.0.1:11434"


def test_cli_show_context_makes_no_model_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    (tmp_path / "profile.json").write_text('{"goal": "retire at 60"}')
    cli.main(["import", str(HISTORY)])
    capsys.readouterr()
    assert cli.main(["advise", "--show-context"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["user_profile"] == {"goal": "retire at 60"}


def test_cli_advise_refuses_remote_host(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    monkeypatch.setenv("OLLAMA_HOST", "http://evil.example")
    cli.main(["import", str(HISTORY)])
    assert cli.main(["advise"]) == 1
    assert "non-local" in capsys.readouterr().err
