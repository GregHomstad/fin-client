import json

from reportlab.platypus import Paragraph

from fin_client import advisor as adv
from fin_client import cli, goals, mdlite, report, snapshot, store
from fin_client.categorize import DEFAULT_RULES
from fin_client.importers import import_file
from fin_client.paths import REPO_ROOT
from fin_client.persona import PLAN_REQUEST

HISTORY = REPO_ROOT / "samples" / "synthetic_history.csv"
NARRATIVE = """# Executive summary
Pay the **24% card** first.

## Short term
- Build a one-month buffer
- Capture the *employer match*
1. Do this
2) Then this <script>alert(1)</script>
---
Plain line & more
"""


def test_markdown_lite_converts_and_escapes():
    styles = {k: None for k in ("h2", "h3", "body", "bullet")}
    from reportlab.lib.styles import getSampleStyleSheet
    ss = getSampleStyleSheet()
    styles = {"h2": ss["Heading2"], "h3": ss["Heading3"], "body": ss["BodyText"], "bullet": ss["BodyText"]}
    flow = mdlite.to_flowables(NARRATIVE, styles)
    paras = [f.text for f in flow if isinstance(f, Paragraph)]
    assert any("<b>24% card</b>" in t for t in paras) and any("<i>employer match</i>" in t for t in paras)
    assert any(t.startswith("&bull; Build") for t in paras) and any(t.startswith("1. Do this") for t in paras)
    assert not any("<script>" in t for t in paras) and any("&lt;script&gt;" in t for t in paras)
    assert any("&amp; more" in t for t in paras)


def snap_with_goals(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    goals.add_goal(conn, "savings", "Car", target=6000, target_date="2027-12-31", current=500)
    return snapshot.build(import_file(HISTORY), DEFAULT_RULES, {"age": 30}, conn)


def test_pdf_contains_narrative_and_goals_and_is_valid(tmp_path):
    out = report.build_pdf(snap_with_goals(tmp_path), tmp_path / "plan.pdf", NARRATIVE, "stub, local")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 5000
    plain = report.build_pdf(snap_with_goals(tmp_path), tmp_path / "plain.pdf")
    assert len(data) > len(plain.read_bytes())  # the narrative page adds content


def test_fullplan_local_uses_the_plan_request_and_writes_pdf_and_markdown(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    cli.main(["import", str(HISTORY)])
    asked = []

    class Stub:
        def __init__(self, snap, model):
            assert "SNAPSHOT" in adv.SYSTEM_PROMPT and snap["monthly_averages"]["income"] == 2500

        def ask(self, q):
            asked.append(q)
            yield "# Plan\n- step one"

    monkeypatch.setattr(adv, "Advisor", Stub)
    monkeypatch.setattr(adv, "ollama_url", lambda: "http://127.0.0.1:11434")
    assert cli.main(["fullplan"]) == 0
    assert asked == [PLAN_REQUEST]
    pdf = next((tmp_path / "reports").glob("fin-client-full-plan-*.pdf"))
    assert pdf.read_bytes().startswith(b"%PDF") and "step one" in pdf.with_suffix(".md").read_text(encoding="utf-8")
    assert cli.main(["fullplan", "--out", str(REPO_ROOT / "leak.pdf")]) == 1  # never inside the repo
    assert not (REPO_ROOT / "leak.pdf").exists()


def test_fullplan_none_needs_no_model(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    cli.main(["import", str(HISTORY)])
    assert cli.main(["fullplan", "--narrative", "none"]) == 0
    assert not list((tmp_path / "reports").glob("*.md"))


def test_fullplan_claude_requires_credential_and_a_typed_yes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    cli.main(["import", str(HISTORY)])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert cli.main(["fullplan", "--narrative", "claude"]) == 1
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    sent = []
    monkeypatch.setattr(adv, "claude_stream", lambda p: sent.append(p) or iter(["x"]))
    out = []
    args = type("A", (), {"narrative": "claude", "model": None, "include_merchants": False, "out": None})()
    conn = store.connect()
    assert cli.fullplan_cmd(args, conn, ask=lambda _p: "n", say=out.append) == 0 and sent == []
    assert any("EXACTLY WHAT WILL BE SENT" in x for x in out) and "Not sent" in out[-1]
    assert cli.fullplan_cmd(args, conn, ask=lambda _p: "y", say=out.append) == 0 and len(sent) == 1
    assert json.dumps(sent[0]).count("Build my full wealth maximization plan") == 1
