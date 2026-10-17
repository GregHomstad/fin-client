"""How complete is the imported data? Plain checks that explain why averages and plans may not look real."""
import calendar
import statistics
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fin_client.models import Transaction


def analyze(txns: list[Transaction], accts: list[dict], profile: dict | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    profile = profile or {}
    if not txns:
        return {"months": [], "accounts": [], "warnings": ["No transactions imported yet."]}
    by_month: dict[str, dict] = defaultdict(lambda: {"n": 0, "first": 99, "last": 0, "in": Decimal(0), "out": Decimal(0)})
    by_acct: dict[str, dict] = defaultdict(lambda: {"n": 0, "first": None, "last": None})
    for t in txns:
        m = by_month[t.date.isoformat()[:7]]
        m["n"] += 1
        m["first"], m["last"] = min(m["first"], t.date.day), max(m["last"], t.date.day)
        if t.amount > 0:
            m["in"] += t.amount
        else:
            m["out"] += -t.amount
        a = by_acct[t.account]
        a["n"] += 1
        a["first"] = t.date if a["first"] is None else min(a["first"], t.date)
        a["last"] = t.date if a["last"] is None else max(a["last"], t.date)
    months = []
    for k in sorted(by_month):
        m = by_month[k]
        y, mo = map(int, k.split("-"))
        days = calendar.monthrange(y, mo)[1]
        months.append({"month": k, "transactions": m["n"], "first_day": m["first"], "last_day": m["last"], "days_in_month": days,
                       "money_in": float(m["in"]), "money_out": float(m["out"]),
                       "span_days": m["last"] - m["first"] + 1, "looks_partial": (m["last"] - m["first"] + 1) < days * 0.6})
    last = max(t.date for t in txns)
    warnings = []
    lag = (today - last).days
    if lag > 7:
        warnings.append(f"The newest transaction is {last.isoformat()}, {lag} days ago. Import a fresh export to bring it current.")
    empty = [a["name"] for a in accts if a["transactions"] == 0 and a["type"] in ("checking", "savings", "credit_card")]
    if empty:
        warnings.append(f"No transactions imported for {len(empty)} account(s): {', '.join(empty)}. Spending on them is missing, "
                        "so spending and surplus are understated.")
    partial = [m["month"] for m in months if m["looks_partial"]]
    if partial:
        warnings.append("These months only have activity on part of the month, so they look low: " + ", ".join(partial) + ".")
    inc = [m["money_in"] for m in months]
    if len(inc) >= 3:
        med = statistics.median(inc)
        spikes = [m["month"] for m in months if med and m["money_in"] > 3 * max(med, 1)]
        if spikes:
            warnings.append("Unusually large money-in in " + ", ".join(spikes) + " (one-off deposits or transfers). "
                            "Averages that include them are not typical.")
        zero = [m["month"] for m in months if m["money_in"] < 200]
        if zero:
            warnings.append("Almost no money in during " + ", ".join(zero) + ". If you were paid then, the pay went to an account that is not imported.")
    net = profile.get("net_per_paycheck")
    if net:
        n = sum(1 for t in txns if abs(float(t.amount) - float(net)) < 0.01)
        warnings.append(f"Your pay stub says take-home is {net:,.2f} per check; {n} deposit(s) of exactly that amount are in the imported data.")
    return {"months": months, "accounts": [{"name": k, "transactions": v["n"], "first": v["first"].isoformat(), "last": v["last"].isoformat()}
                                           for k, v in sorted(by_acct.items())] +
                                          [{"name": n, "transactions": 0, "first": None, "last": None} for n in empty],
            "warnings": warnings, "newest": last.isoformat(), "oldest": min(t.date for t in txns).isoformat()}
