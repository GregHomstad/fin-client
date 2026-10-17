from datetime import date
from decimal import Decimal
from pathlib import Path

from fin_client import cli, pdf_import, store
from fin_client.importers import pdf_bank


def stmt(last4="1234", start="9/01/26", end="9/30/26", prev="1,000.00", rows=(), cur=None, extra=()):
    """Synthetic statement text in the same layout as the real ones (all names and numbers invented)."""
    lines = [f"  Account Number XXXXXXXXXXXX{last4}   Statement Dates {start} thru {end}",
             f"  Previous Balance                  {prev}   Days in the statement period  30"]
    lines += list(extra)
    if cur is not None:
        lines.append(f"  Current Balance                   {cur}")
    lines.append("  Date  Description                 Reference         Amount            Balance")
    for d, first, cont, amt, bal in rows:
        lines.append(f"            {d} {first}          09/05/26 11111111 22222222        {amt}            {bal}")
        lines += [f"                  {c}" for c in cont]
    return lines


ROWS = [("9/02", "POS PURCH", ["SAMPLE COFFEE SHOP", "100 MAIN ST ANYTOWN AR"], "4.50-", "995.50"),
        ("9/02", "POS PURCH", ["SAMPLE COFFEE SHOP", "100 MAIN ST ANYTOWN AR"], "4.50-", "991.00"),  # identical, same day
        ("9/15", "ACH CREDIT", ["EXAMPLE EMPLOYER PAYROLL"], "2,000.00", "2,991.00")]


def test_statement_parses_reconciles_and_joins_continuation_lines():
    [s] = pdf_bank.parse_text(stmt(rows=ROWS, cur="2,991.00"))
    assert s.reconciled and s.last4 == "1234" and (s.start, s.end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert [t.amount for t in s.txns] == [Decimal("-4.50"), Decimal("-4.50"), Decimal("2000.00")]
    assert "SAMPLE COFFEE SHOP 100 MAIN ST ANYTOWN AR" in s.txns[0].description
    assert s.txns[2].date == date(2026, 9, 15)


def test_wrong_totals_do_not_reconcile_and_are_not_trusted():
    [s] = pdf_bank.parse_text(stmt(rows=ROWS, cur="2,990.00"))
    assert not s.reconciled and "off by" in s.problem


def test_year_rolls_over_a_december_to_january_statement():
    rows = [("12/30", "POS PURCH", ["SAMPLE STORE"], "10.00-", "990.00"), ("1/02", "POS PURCH", ["SAMPLE STORE"], "5.00-", "985.00")]
    [s] = pdf_bank.parse_text(stmt(start="12/20/25", end="1/19/26", rows=rows, cur="985.00"))
    assert [t.date for t in s.txns] == [date(2025, 12, 30), date(2026, 1, 2)]


def test_lines_without_a_running_balance_are_memo_lines_not_transactions():
    text = stmt(rows=ROWS, cur="2,991.00") + ["            9/30   Service Charge Fee                         5.00"]
    [s] = pdf_bank.parse_text(text)
    assert len(s.txns) == 3 and s.reconciled


def test_summary_only_service_charge_is_added_only_if_it_makes_the_statement_balance():
    summary = ["  Service Charge                    5.00   Interest Earned   .08", "  Interest Paid                      .08"]
    rows = [("9/15", "ACH CREDIT", ["EXAMPLE EMPLOYER PAYROLL"], "2,000.00", "3,000.00")]
    [s] = pdf_bank.parse_text(stmt(rows=rows, cur="2,995.08", extra=summary))
    assert s.reconciled and [t.description for t in s.txns][-2:] == ["Service charge (from statement summary)", "Interest paid (from statement summary)"]
    [bad] = pdf_bank.parse_text(stmt(rows=rows, cur="2,900.00", extra=summary))
    assert not bad.reconciled and len(bad.txns) == 1  # nothing invented


def test_repeated_prints_of_the_same_period_keep_the_longest():
    a = pdf_bank.parse_text(stmt(end="9/15/26", rows=ROWS[:2], cur="991.00"))
    b = pdf_bank.parse_text(stmt(end="9/30/26", rows=ROWS, cur="2,991.00"))
    [keep] = pdf_bank.select_supersets(a + b)
    assert keep.end == date(2026, 9, 30) and len(keep.txns) == 3


def test_store_keeps_genuinely_identical_same_day_rows_but_reimport_adds_nothing(tmp_path):
    [s] = pdf_bank.parse_text(stmt(rows=ROWS, cur="2,991.00"))
    conn = store.connect(tmp_path / "t.db")
    assert store.add(conn, s.txns) == 3
    assert store.add(conn, s.txns) == 0
    assert len(store.all_transactions(conn, match_transfers=False)) == 3


def test_old_database_is_migrated_without_losing_rows(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE transactions (date TEXT NOT NULL, description TEXT NOT NULL, amount TEXT NOT NULL, account TEXT NOT NULL, "
              "bank_category TEXT NOT NULL DEFAULT '', UNIQUE(date, description, amount, account))")
    c.execute("INSERT INTO transactions VALUES ('2026-09-02','X','-4.50','A','Food')")
    c.commit(); c.close()
    conn = store.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("SELECT bank_category, occurrence FROM transactions").fetchall() == [("Food", 1)]


def test_import_pdfs_end_to_end_with_account_naming_and_skips(monkeypatch, tmp_path):
    good = stmt(last4="0394", rows=ROWS, cur="2,991.00")
    bad = stmt(last4="4845", rows=ROWS, cur="1.00")
    monkeypatch.setattr(pdf_bank, "read_pdf_lines", lambda path: {"a.pdf": good, "b.pdf": bad, "c.pdf": ["not a statement"]}[Path(path).name])
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    files = [tmp_path / n for n in ("a.pdf", "b.pdf", "c.pdf")]
    for f in files:
        f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pdf_bank, "parse_pdf", lambda path: pdf_bank.parse_text(pdf_bank.read_pdf_lines(Path(path))))
    conn = store.connect()
    rep = pdf_import.import_pdfs(conn, files, {"0394": "Simmons checking"})
    assert rep["added"] == 3 and rep["not_bank_statements"] == 1
    assert rep["accounts"][0]["account"] == "Simmons checking" and rep["skipped"][0]["problem"].startswith("does not reconcile")
    assert {t.account for t in store.all_transactions(conn, match_transfers=False)} == {"Simmons checking"}
    again = pdf_import.import_pdfs(conn, files, {"0394": "Simmons checking"})
    assert again["added"] == 0  # idempotent
    dry = pdf_import.import_pdfs(store.connect(tmp_path / "other.db"), files, {"0394": "Simmons checking"}, dry_run=True)
    assert dry["added"] == 3
    assert cli.main(["import-pdfs", str(tmp_path), "--map", "0394=Simmons checking", "--dry-run"]) == 0


def test_page_break_headers_do_not_leak_into_descriptions():
    lines = stmt(rows=ROWS[:1], cur="995.50")
    lines.insert(-1, "                  Date 9/30/26   Page 2    Primary Account XXXXXXXXXXXX1234")
    lines.insert(-1, "                  Account Number XXXXXXXXXXXX1234   Statement Dates 9/01/26 thru 9/30/26")
    [s] = pdf_bank.parse_text(lines)
    assert all("Primary Account" not in t.description and "Page" not in t.description for t in s.txns)
    assert s.reconciled and "SAMPLE COFFEE SHOP" in s.txns[0].description
