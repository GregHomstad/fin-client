from datetime import date
from decimal import Decimal

import pytest

from fin_client import accounts, snapshot, store, transfers
from fin_client.models import Transaction


def tx(d, desc, amt, acct, bank=""):
    return Transaction(date(2026, 9, d), desc, Decimal(amt), acct, bank)


def test_guess_type():
    assert accounts.guess_type("VISA SIGNATURE CARD") == "credit_card"
    assert accounts.guess_type("ESSENTIAL CHECKING") == "checking"
    assert accounts.guess_type("SAVINGS") == "savings"
    assert accounts.guess_type("Roth IRA") == "retirement"


def test_accounts_created_from_transactions_and_net_worth(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.add(conn, [tx(1, "PAY", "100", "MY CHECKING"), tx(2, "STORE", "-5", "MY VISA CARD")])
    accts = accounts.list_accounts(conn)
    assert {a["name"]: a["type"] for a in accts} == {"MY CHECKING": "checking", "MY VISA CARD": "credit_card"}
    accounts.upsert(conn, "MY CHECKING", "checking", "1,000.50")
    accounts.upsert(conn, "MY VISA CARD", "credit_card", "-250", apr_pct="24", min_payment="35")  # sign normalized
    accounts.upsert(conn, "401k", "retirement", 20000)
    nw = accounts.net_worth(accounts.list_accounts(conn))
    assert nw["assets"] == 21000.5 and nw["liabilities"] == 250 and nw["net_worth"] == 20750.5
    assert nw["missing_balances"] == []


def test_missing_balances_are_reported_not_counted(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    accounts.upsert(conn, "Savings", "savings", 500)
    accounts.upsert(conn, "Card", "credit_card")
    nw = accounts.net_worth(accounts.list_accounts(conn))
    assert nw["net_worth"] == 500 and nw["missing_balances"] == ["Card"]


def test_validation(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    with pytest.raises(ValueError):
        accounts.upsert(conn, "", "checking")
    with pytest.raises(ValueError):
        accounts.upsert(conn, "x", "bogus")
    with pytest.raises(ValueError):
        accounts.upsert(conn, "x", "checking", "abc")


def test_effective_profile_feeds_planning_but_explicit_values_win(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    accounts.upsert(conn, "Checking", "checking", 1000)
    accounts.upsert(conn, "Savings", "savings", 2000)
    accounts.upsert(conn, "Card", "credit_card", 3000, apr_pct=24, min_payment=90)
    accounts.upsert(conn, "401k", "retirement", 50000)
    accts = accounts.list_accounts(conn)
    p = accounts.effective_profile({}, accts)
    assert p["cash_savings"] == 3000 and p["retirement_balance"] == 50000
    assert p["debts"] == [{"name": "Card", "balance": 3000.0, "apr_pct": 24.0, "min_payment": 90.0}]
    p2 = accounts.effective_profile({"cash_savings": 10, "debts": [{"name": "Other", "balance": 1, "apr_pct": 5}]}, accts)
    assert p2["cash_savings"] == 10 and {d["name"] for d in p2["debts"]} == {"Other", "Card"}
    assert snapshot.profile_for(conn)["retirement_balance"] == 50000  # via the store connection


def test_card_payment_is_matched_and_not_double_counted():
    t = [tx(5, "ONLINE PAYMENT TO CARD", "-300", "CHECKING"), tx(6, "PAYMENT THANK YOU", "300", "CARD"),
         tx(2, "GROCERY", "-40", "CARD"), tx(3, "PAYROLL", "1000", "CHECKING")]
    marked = {x.description: x.bank_category for x in transfers.mark_internal(t)}
    assert marked["ONLINE PAYMENT TO CARD"] == marked["PAYMENT THANK YOU"] == transfers.MARK
    assert marked["GROCERY"] == "" and marked["PAYROLL"] == ""


def test_unrelated_same_amount_far_apart_is_not_matched():
    t = [tx(1, "STORE A", "-50", "CHECKING"), tx(20, "REFUND B", "50", "CARD")]
    assert all(x.bank_category == "" for x in transfers.mark_internal(t))


def test_same_account_and_one_to_one_matching():
    same = [tx(1, "TRANSFER", "-50", "A"), tx(1, "TRANSFER", "50", "A")]
    assert all(x.bank_category == "" for x in transfers.mark_internal(same))
    two_out_one_in = [tx(1, "PAYMENT", "-50", "A"), tx(1, "PAYMENT", "-50", "A"), tx(1, "PAYMENT RECEIVED", "50", "B")]
    assert sum(x.bank_category == transfers.MARK for x in transfers.mark_internal(two_out_one_in)) == 2


def test_cashflow_excludes_matched_transfers(tmp_path):
    from fin_client import analytics
    conn = store.connect(tmp_path / "t.db")
    store.add(conn, [tx(1, "PAYROLL", "2000", "CHECKING"), tx(5, "ONLINE PAYMENT", "-300", "CHECKING"),
                     tx(5, "PAYMENT THANK YOU", "300", "CARD"), tx(3, "GROCERY", "-300", "CARD")])
    m = analytics.cashflow(store.all_transactions(conn))[0]
    assert m.income == 2000 and m.spending == 300  # the payment counted once, as the grocery spend


def test_set_aside_money_is_excluded_from_cash_and_prefunds_the_debt(tmp_path):
    from fin_client import goals
    conn = store.connect(tmp_path / "t.db")
    accounts.upsert(conn, "Checking", "checking", 800)
    accounts.upsert(conn, "Savings", "savings", 9000, earmarked=7000, earmark_for="Family loan")
    accounts.upsert(conn, "Family loan", "loan", 7000, apr_pct=0)
    accts = accounts.list_accounts(conn)
    assert accounts.prefunded_debts(accts) == {"Family loan": 7000.0}
    p = accounts.effective_profile({}, accts)
    assert p["cash_savings"] == 2800 and "debts" not in p  # 800 + 9000 - 7000; the funded loan is not a payoff target
    assert accounts.net_worth(accts)["set_aside"] == 7000
    plan = goals.build_plan([], accts, 1000, 300, date(2026, 10, 17))
    assert plan["cash_on_hand"] == 2800 and plan["prefunded"] == [{"debt": "Family loan", "amount": 7000.0, "from": "Savings"}]
    assert plan["debts"] == [] and any("set aside" in a for a in plan["assumptions"])
    # partial set-aside does not prefund
    accounts.upsert(conn, "Savings", "savings", 9000, earmarked=3000, earmark_for="Family loan")
    assert accounts.prefunded_debts(accounts.list_accounts(conn)) == {}
    with pytest.raises(ValueError):
        accounts.upsert(conn, "Savings", "savings", 100, earmarked=500)
    with pytest.raises(ValueError):
        accounts.upsert(conn, "Card", "credit_card", 100, earmarked=50)
