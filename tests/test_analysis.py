import json
from decimal import Decimal

from fin_client import analytics, cli, recurring
from fin_client.categorize import DEFAULT_RULES, categorize, load_rules
from fin_client.importers import import_file
from fin_client.paths import REPO_ROOT

HISTORY = REPO_ROOT / "samples" / "synthetic_history.csv"


def test_categorize_defaults():
    assert categorize("EXAMPLE APARTMENTS RENT", Decimal(-1100), DEFAULT_RULES) == "Housing"
    assert categorize("SAMPLE GROCERY MART #12", Decimal(-50), DEFAULT_RULES) == "Groceries"
    assert categorize("MYSTERY VENDOR", Decimal(-5), DEFAULT_RULES) == "Uncategorized"
    assert categorize("MYSTERY DEPOSIT", Decimal(5), DEFAULT_RULES) == "Income"


def test_user_rules_take_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    (tmp_path / "rules.json").write_text(json.dumps({"Hobbies": ["mystery vendor"]}))
    rules = load_rules()
    assert categorize("MYSTERY VENDOR", Decimal(-5), rules) == "Hobbies"
    assert categorize("EXAMPLE APARTMENTS RENT", Decimal(-1), rules) == "Housing"


def test_recurring_detection():
    found = {r.merchant: r for r in recurring.detect(import_file(HISTORY))}
    assert found["EXAMPLE APARTMENTS RENT"].cadence == "monthly"
    assert found["EXAMPLE APARTMENTS RENT"].monthly_cost == Decimal("-1100.00")
    assert found["FAKE STREAMING"].count == 5
    assert found["ACME CORP PAYROLL"].amount == Decimal("2500.00")
    assert "DEMO ELECTRIC CO" in found  # varies ~10%, within tolerance
    assert "SAMPLE GROCERY MART" not in found  # amounts too variable
    assert "DEMO PHARMACY" not in found  # one-off


def test_cashflow_excludes_transfers():
    months = analytics.cashflow(import_file(HISTORY), DEFAULT_RULES)
    assert [m.month for m in months] == ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    jun = months[0]
    assert jun.income == Decimal("2500.00")
    assert "Transfers" not in jun.by_category
    assert jun.spending == Decimal("1100.00") + Decimal("72.10") + Decimal("15.99") + Decimal("88.10") + Decimal("64.30")
    assert jun.savings_rate == (jun.net / jun.income * 100).quantize(Decimal("0.1"))


def test_averages_drop_partial_edge_months():
    months = analytics.cashflow(import_file(HISTORY), DEFAULT_RULES)
    avg = analytics.averages(months)
    assert avg["months_used"] == 3
    assert avg["avg_income"] == Decimal("2500.00")


def test_cli_analysis_commands(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    assert cli.main(["import", str(HISTORY)]) == 0
    capsys.readouterr()
    assert cli.main(["recurring"]) == 0
    assert "EXAMPLE APARTMENTS RENT" in capsys.readouterr().out
    assert cli.main(["cashflow"]) == 0
    assert "avg_net" in capsys.readouterr().out
    assert cli.main(["categories"]) == 0
    out = capsys.readouterr().out
    assert "Housing" in out and "MYSTERY VENDOR" in out
