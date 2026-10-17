import json
from datetime import timedelta
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
    assert categorize("MYSTERY VENDOR", Decimal(-1), rules, "Shopping") == "Hobbies"  # your keywords beat the bank label


def test_recurring_detection():
    found = {r.merchant: r for r in recurring.detect(import_file(HISTORY))}
    assert found["EXAMPLE APARTMENTS RENT"].cadence == "monthly"
    assert found["EXAMPLE APARTMENTS RENT"].monthly_cost == Decimal("-1100.00")
    assert found["FAKE STREAMING"].count == 5
    assert found["ACME CORP PAYROLL"].amount == Decimal("2500.00")
    assert "DEMO ELECTRIC" in found  # varies ~10%, within tolerance
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


def test_bank_category_is_used_as_fallback_after_keywords():
    assert categorize("MYSTERY VENDOR", Decimal(-5), DEFAULT_RULES, "Food & Drink") == "Food & Drink"
    assert categorize("EXAMPLE APARTMENTS RENT", Decimal(-5), DEFAULT_RULES, "Food & Drink") == "Food & Drink"
    assert categorize("EXAMPLE APARTMENTS RENT", Decimal(-5), DEFAULT_RULES, "Uncategorized Expense") == "Housing"
    assert categorize("X", Decimal(-5), DEFAULT_RULES, "Food/Groceries") == "Groceries"
    assert categorize("X", Decimal(-5), DEFAULT_RULES, "Housing/Home Repair") == "Housing"
    assert categorize("X", Decimal(-5), DEFAULT_RULES, "Healthcare - HSA Spending Account") == "Health"
    assert categorize("X", Decimal(-5), DEFAULT_RULES, "Transfer Between Accounts/Credit Card Payment") == "Transfers"
    assert categorize("X", Decimal(-5), DEFAULT_RULES, "Online Transfer") == "Transfers"
    assert categorize("X", Decimal(-5), DEFAULT_RULES, "Uncategorized") == "Uncategorized"
    rules = {**DEFAULT_RULES, "_bank_categories": {"food & drink": "Dining"}}
    assert categorize("X", Decimal(-5), rules, "Food & Drink") == "Dining"


def test_store_migrates_and_backfills_bank_category(tmp_path):
    import sqlite3

    from fin_client import store
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE transactions (date TEXT NOT NULL, description TEXT NOT NULL, amount TEXT NOT NULL, "
                "account TEXT NOT NULL, UNIQUE(date, description, amount, account))")
    old.execute("INSERT INTO transactions VALUES ('2026-09-03','SAMPLE GROCERY MART','-86.42','SYNTHETIC CHECKING')")
    old.commit(); old.close()
    conn = store.connect(db)
    store.add(conn, import_file(REPO_ROOT / "samples" / "synthetic_preamble.csv"))
    got = {t.description: t.bank_category for t in store.all_transactions(conn)}
    assert got["SAMPLE GROCERY MART"] == "Food"  # backfilled, not duplicated
    assert len(got) == 3


def test_recurring_active_yearly_and_stopped_with_follow_up():
    from datetime import date as d

    from fin_client.models import Transaction as T
    t = []
    for m in range(1, 11):  # a monthly charge that is still going
        t.append(T(d(2026, m, 5), "STREAMING PLUS 123", Decimal("-15.99"), "Chk"))
    for m in (3, 4, 5, 6):  # a monthly charge that ended in June
        t.append(T(d(2026, m, 9), "OLD GYM CLUB", Decimal("-30"), "Chk"))
    t += [T(d(2025, 10, 20), "ANNUAL ANTIVIRUS", Decimal("-59.99"), "Chk"), T(d(2026, 10, 19), "ANNUAL ANTIVIRUS", Decimal("-59.99"), "Chk")]
    for i in range(3):  # an old deposit pattern that stopped
        t.append(T(d(2025, 11, 6) + timedelta(days=14 * i), "ADVANCE APP DEPOSIT", Decimal("500"), "Chk"))
    t.append(T(d(2026, 10, 25), "OTHER ACCOUNT ITEM", Decimal("-1"), "Chk"))  # pushes the newest date forward
    got = recurring.analyze(t, since=d(2026, 8, 1))
    assert [r.merchant for r in got["active"]] == ["STREAMING PLUS"]
    assert [r.merchant for r in got["yearly"]] == ["ANNUAL ANTIVIRUS"] and got["yearly"][0].cadence == "yearly"
    stopped = {r.merchant: r for r in got["stopped"]}
    assert set(stopped) == {"OLD GYM CLUB", "ADVANCE APP DEPOSIT"}
    assert "account itself is closed" in stopped["OLD GYM CLUB"].follow_up and "deposit" in stopped["ADVANCE APP DEPOSIT"].follow_up
    assert got["active"][0].follow_up == ""


def test_stopped_is_judged_against_that_accounts_newest_data():
    from datetime import date as d

    from fin_client.models import Transaction as T
    t = [T(d(2026, m, 25), "SUB SERVICE", Decimal("-10"), "Short export") for m in (6, 7, 8)]
    t += [T(d(2026, 9, 1), "OTHER", Decimal("-1"), "Short export")]            # this export stops Sep 1
    t += [T(d(2026, 10, 15), "ELSEWHERE", Decimal("-1"), "Long export")]       # another account is newer
    [r] = [x for x in recurring.analyze(t)["active"] if x.merchant == "SUB SERVICE"]
    assert r.status == "active"  # last payment Aug 25 is only 7 days before ITS account's data ends, not 51


def test_a_biller_that_changes_its_description_stays_one_recurring_payment():
    from datetime import date as d

    from fin_client.models import Transaction as T
    t = [T(d(2026, m, 14), "PERSON NAME PAYMENT ATT", Decimal("-117.06"), "Chk") for m in (3, 4, 5, 6, 7)]
    t += [T(d(2026, 8, 4), "WEB 123456789MYW9C PAYMENT ATT", Decimal("-121.50"), "Chk"),
          T(d(2026, 9, 2), "WEB 123456789MYW9G PAYMENT ATT", Decimal("-122.50"), "Chk")]
    t += [T(d(2026, 9, 30), "SOMETHING ELSE", Decimal("-5"), "Chk")]
    got = recurring.analyze(t)
    assert len(got["active"]) == 1 and got["active"][0].count == 7 and got["active"][0].last_date == d(2026, 9, 2)
    assert got["stopped"] == []
    far = [T(d(2026, 1, 5), "OLD BILLER ATT", Decimal("-50"), "Chk")] + [T(d(2026, m, 5), "NEW BILLER ATT", Decimal("-50"), "Chk") for m in (8, 9, 10)]
    assert len(recurring._merge_aliases({("OLD BILLER ATT", False): far[:1], ("NEW BILLER ATT", False): far[1:]})) == 2  # 7 months apart: not merged
