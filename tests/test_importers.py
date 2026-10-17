from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fin_client import cli, store
from fin_client.importers import import_file
from fin_client.paths import REPO_ROOT, data_dir

SAMPLES = REPO_ROOT / "samples"


def test_csv_signed_amount():
    t = import_file(SAMPLES / "synthetic_checking.csv")
    assert len(t) == 6
    assert t[0].amount == Decimal("2500.00") and t[1].amount == Decimal("-1100.00")
    assert t[0].date == date(2026, 9, 1)


def test_csv_debit_credit_columns():
    t = import_file(SAMPLES / "synthetic_debit_credit.csv")
    assert [x.amount for x in t] == [Decimal("2500.00"), Decimal("-1100.00"), Decimal("-12.50")]


def test_ofx():
    t = import_file(SAMPLES / "synthetic_bank.ofx")
    assert [x.amount for x in t] == [Decimal("2500.00"), Decimal("-1100.00"), Decimal("-86.42")]
    assert t[1].description == "EXAMPLE APARTMENTS RENT"


def test_unsupported_format(tmp_path):
    with pytest.raises(ValueError):
        import_file(tmp_path / "x.pdf")


def test_bad_csv_headers(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        import_file(f)


def test_store_dedupes_and_summarizes(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    txns = import_file(SAMPLES / "synthetic_checking.csv")
    assert store.add(conn, txns) == 6
    assert store.add(conn, txns) == 0
    s = store.monthly_summary(conn)["2026-09"]
    assert s["income"] == Decimal("2500.00")
    assert s["spending"] == Decimal("-1282.76")


def test_data_dir_refuses_repo(monkeypatch):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(REPO_ROOT / "data"))
    with pytest.raises(RuntimeError):
        data_dir()


def test_cli_roundtrip(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    assert cli.main(["import", str(SAMPLES / "synthetic_checking.csv")]) == 0
    assert cli.main(["summary"]) == 0
    assert "2026-09" in capsys.readouterr().out
    assert cli.main(["import", str(tmp_path / "nope.csv")]) == 1


def test_csv_with_account_preamble_and_account_column():
    t = import_file(SAMPLES / "synthetic_preamble.csv")
    assert [x.amount for x in t] == [Decimal("2500.00"), Decimal("-1100.00"), Decimal("-86.42")]
    assert {x.account for x in t} == {"SYNTHETIC CHECKING"}  # taken from the Account column
    explicit = import_file(SAMPLES / "synthetic_preamble.csv", account="main")
    assert {x.account for x in explicit} == {"main"}
