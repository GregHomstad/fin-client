import json
from datetime import date
from decimal import Decimal

from fin_client import labeler
from fin_client.categorize import categorize, load_rules
from fin_client.models import Transaction


def tx(desc, amt, bank=""):
    return Transaction(date(2026, 9, 1), desc, Decimal(amt), "a", bank)


def test_default_keyword_drops_store_numbers():
    assert labeler.default_keyword("SAMPLE GROCERY MART #1234 SPRINGDALE") == "SAMPLE GROCERY MART"
    assert labeler.default_keyword("12345") == "12345"


def test_queue_is_sorted_by_total_and_skips_categorized(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    t = [tx("SMALL THING", -5, "Uncategorized Expense"), tx("BIG THING", -900, "Uncategorized Expense"),
         tx("BIG THING", -100, "Uncategorized Expense"), tx("KNOWN", -50, "Shopping")]
    q = labeler.uncategorized_merchants(t, load_rules())
    assert [r[0] for r in q] == ["BIG THING", "SMALL THING"] and q[0][1:] == (2, Decimal(1000))


def test_interactive_session_saves_rules_that_apply_next_time(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    t = [tx("BIG LENDER 4455", -1500, "Uncategorized Expense"), tx("MYSTERY SHOP", -20, "Uncategorized Expense"),
         tx("LATER ONE", -3, "Uncategorized Expense")]
    answers = iter(["?", "1", "", "", "q"])  # list, Housing + default keyword, skip MYSTERY SHOP, quit
    said = []
    saved = labeler.run(t, ask=lambda _p: next(answers), say=said.append)
    assert saved == 1
    assert json.loads((tmp_path / "rules.json").read_text()) == {"Housing": ["BIG LENDER"]}
    assert categorize("BIG LENDER 9999", Decimal(-1), load_rules(), "Uncategorized Expense") == "Housing"
    assert any("Housing" in s for s in said)


def test_bad_keyword_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("FIN_CLIENT_DATA", str(tmp_path))
    answers = iter(["Housing", "NOPE"])
    assert labeler.run([tx("BIG LENDER", -9, "Uncategorized Expense")], ask=lambda _p: next(answers),
                       say=lambda s: None) == 0
    assert not (tmp_path / "rules.json").exists()
