import json

from fin_client import cli, report, snapshot
from fin_client.categorize import DEFAULT_RULES
from fin_client.importers import import_file
from fin_client.paths import REPO_ROOT

HISTORY = REPO_ROOT / "samples" / "synthetic_history.csv"
PROFILE = {"age": 30, "retire_age": 65, "cash_savings": 1500, "retirement_balance": 20000,
           "monthly_retirement_contribution": 300, "target_retirement_spending_monthly": 4000,
           "filing_status": "single", "annual_gross_income": 60000, "traditional_401k_annual": 3000,
           "debts": [{"name": "card", "balance": 3000, "apr_pct": 24, "min_payment": 90}]}


def test_pdf_is_written_and_valid(tmp_path):
    snap = snapshot.build(import_file(HISTORY), DEFAULT_RULES, PROFILE)
    out = report.build_pdf(snap, tmp_path / "r.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 3000


def test_pdf_works_with_minimal_profile(tmp_path):
    snap = snapshot.build(import_file(HISTORY), DEFAULT_RULES, {})
    assert report.build_pdf(snap, tmp_path / "min.pdf").read_bytes().startswith(b"%PDF")


def test_refuses_to_write_inside_repo():
    snap = snapshot.build(import_file(HISTORY), DEFAULT_RULES, {})
    try:
        report.build_pdf(snap, REPO_ROOT / "leak.pdf")
    except RuntimeError as e:
        assert "inside the repo" in str(e)
    else:
        raise AssertionError("should have refused")
    assert not (REPO_ROOT / "leak.pdf").exists()


def test_cli_report_default_location(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    (tmp_path / "profile.json").write_text(json.dumps(PROFILE))
    cli.main(["import", str(HISTORY)])
    capsys.readouterr()
    assert cli.main(["report"]) == 0
    assert list((tmp_path / "reports").glob("fin-client-report-*.pdf"))
    assert cli.main(["report", "--out", str(REPO_ROOT / "x.pdf")]) == 1
