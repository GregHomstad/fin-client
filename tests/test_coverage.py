from datetime import date
from decimal import Decimal

from fin_client import cli, coverage, report, store
from fin_client.categorize import DEFAULT_RULES
from fin_client.models import Transaction
from fin_client.paths import REPO_ROOT


def tx(y, m, d, desc, amt, acct="Chk"):
    return Transaction(date(y, m, d), desc, Decimal(amt), acct)


def acct(name, type_, n):
    return {"name": name, "type": type_, "kind": "asset", "balance": None, "transactions": n}


def test_coverage_explains_why_numbers_look_wrong():
    t = [tx(2026, 6, 4, "BIG DEPOSIT", "21000"), tx(2026, 6, 30, "RENT", "-900"),
         tx(2026, 7, 1, "RENT", "-900"), tx(2026, 7, 29, "FOOD", "-50"),
         tx(2026, 8, 17, "PAY", "1119"), tx(2026, 8, 31, "FOOD", "-40"),
         tx(2026, 9, 8, "PAY", "2126.44"), tx(2026, 9, 30, "FOOD", "-30"),
         tx(2026, 10, 1, "FOOD", "-20")]
    accts = [acct("Chk", "checking", 9), acct("Simmons", "checking", 0), acct("Card", "credit_card", 0)]
    cov = coverage.analyze(t, accts, {"net_per_paycheck": 2126.44}, today=date(2026, 10, 17))
    text = " ".join(cov["warnings"])
    assert "16 days ago" in text and "Simmons, Card" in text and "understated" in text
    assert "Unusually large money-in in 2026-06" in text and "Almost no money in during 2026-07" in text
    assert "1 deposit(s) of exactly that amount" in text
    aug = next(m for m in cov["months"] if m["month"] == "2026-08")
    assert aug["first_day"] == 17 and aug["last_day"] == 31 and aug["money_in"] == 1119
    assert [a["name"] for a in cov["accounts"]][-2:] == ["Simmons", "Card"]
    assert coverage.analyze([], [], {})["warnings"] == ["No transactions imported yet."]


def test_all_transactions_pdf_builds_with_coverage_page(tmp_path):
    t = [tx(2026, 9, 8, "PAYROLL ACME", "2126.44"), tx(2026, 9, 9, "SAMPLE GROCERY MART", "-40.10"),
         tx(2026, 9, 10, "RENT EXAMPLE APARTMENTS", "-900")]
    cov = coverage.analyze(t, [acct("Chk", "checking", 3)], {}, today=date(2026, 9, 30))
    out = report.build_transactions_pdf(t, DEFAULT_RULES, cov, tmp_path / "all.pdf")
    assert out.read_bytes().startswith(b"%PDF") and out.stat().st_size > 2500


def test_cli_transactions_command_and_repo_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    cli.main(["import", str(REPO_ROOT / "samples" / "synthetic_history.csv")])
    assert cli.main(["transactions"]) == 0
    assert list((tmp_path / "reports").glob("fin-client-all-transactions-*.pdf"))
    assert cli.main(["transactions", "--out", str(REPO_ROOT / "leak.pdf")]) == 1
