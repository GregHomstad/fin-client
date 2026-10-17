"""Getting back on track: computed scenarios, not advice.

Three questions, answered with your own numbers:
  1. Where do I stand?  (net worth, usable cash, how many months of must-pay costs the cash covers)
  2. What if income stops?  (contract ends: cash needed vs cash on hand for 1/2/3/6 months)
  3. What does trimming discretionary spending change?  (freed dollars, new debt-free date)
Every figure is arithmetic on the snapshot, accounts and goals; the choices stay yours.
"""
from datetime import date

from fin_client import accounts, goals

DISCRETIONARY = ["Dining", "Alcohol & Tobacco", "Shopping", "Entertainment", "Subscriptions", "Fitness & Personal Care",
                 "Travel", "Gifts & Donations"]
GAP_MONTHS = (1, 2, 3, 6)
TRIMS = (10, 25, 50)


def _ym_index(ym: str | None) -> int | None:
    return None if not ym else int(ym[:4]) * 12 + int(ym[5:7])


def _idx(ym: str) -> int:
    return int(ym[:4]) * 12 + int(ym[5:7])


def _label(i: int) -> str:
    m = i % 12 or 12
    return f"{i // 12 if i % 12 else i // 12 - 1}-{m:02d}"


def income_timeline(profile: dict, cash: float, spending: float | None, today: date) -> dict | None:
    """Month-by-month cash under the income you described: pay, family support that steps down, a contract that may end."""
    fs = profile.get("family_support") or {}
    net = profile.get("net_per_paycheck")
    if not (fs or net) or spending is None:
        return None
    take_home = float(net) * 26 / 12 if net else 0.0
    now_i = today.year * 12 + today.month + 1  # index of next month (the plan starts there)
    support_now = float(fs.get("monthly", 0))
    support_after = float(fs.get("after_monthly", 0))
    step_i = _idx(fs["through"]) + 1 if fs.get("through") else None            # first month at the lower support
    end_i = _idx(profile["contract_ends"]) if profile.get("contract_ends") else None  # last month with pay
    horizon = 12
    rows, bal, cur = [], cash, None
    for i in range(now_i, now_i + horizon):
        support = support_after if (step_i is not None and i >= step_i) else support_now
        pay = take_home if (end_i is None or i <= end_i) else 0.0
        key = (round(pay, 2), round(support, 2))
        if cur is None or cur["key"] != key:
            cur = {"key": key, "start": _label(i), "months": 0, "monthly_income": round(pay + support, 2), "pay": round(pay, 2),
                   "support": round(support, 2)}
            rows.append(cur)
        cur["months"] += 1
        cur["end"] = _label(i)
        bal += pay + support - spending
        cur["cash_at_end"] = round(bal, 2)
    for r in rows:
        r.pop("key")
        r["monthly_spending"] = round(spending, 2)
        r["monthly_surplus"] = round(r["monthly_income"] - spending, 2)
    last = rows[-1]
    runs_out = None
    if last["monthly_surplus"] < 0:
        before = last["cash_at_end"] - last["months"] * last["monthly_surplus"]
        runs_out = round(max(before, 0) / -last["monthly_surplus"], 1)
    note = ("Spending is what your imported accounts show since your chosen start month. Card spending that isn't imported yet "
            "is missing, so real surpluses are lower.")
    return {"phases": rows, "months_cash_lasts_in_last_phase": runs_out, "starting_cash": round(cash, 2), "note": note}


def build(surplus: float, essential: float, cat_avg: dict[str, float], accts: list[dict], goal_rows: list[dict],
          profile: dict, today: date | None = None, spending: float | None = None) -> dict:
    today = today or date.today()
    nw = accounts.net_worth(accts)
    prefunded = accounts.prefunded_debts(accts)
    cash = sum(a["balance"] - (a.get("earmarked") or 0) for a in accts
               if a["type"] in ("checking", "savings", "cash") and a["balance"] is not None)
    live_debts = [a for a in accts if a["kind"] == "liability" and (a["balance"] or 0) > 0 and a["name"] not in prefunded]
    minimums = sum(a["min_payment"] or 0 for a in live_debts)
    unknown_min = [a["name"] for a in live_debts if a["min_payment"] is None]
    must_pay = essential + minimums
    pending = sum(float(d.get("amount", 0)) for d in profile.get("pending_deposits", []) if isinstance(d, dict))

    out: dict = {
        "position": {
            "net_worth": nw["net_worth"], "assets": nw["assets"], "liabilities": nw["liabilities"],
            "debt_not_yet_funded": round(sum(a["balance"] for a in live_debts), 2),
            "set_aside_for_debts": round(sum(prefunded.values()), 2),
            "usable_cash": round(cash, 2), "pending_deposits": round(pending, 2),
            "essential_spending_per_month": round(essential, 2), "debt_minimums_per_month": round(minimums, 2),
            "must_pay_per_month": round(must_pay, 2),
            "months_covered_today": round(cash / must_pay, 1) if must_pay else None,
            "months_covered_after_deposits": round((cash + pending) / must_pay, 1) if must_pay else None,
            "monthly_surplus": round(surplus, 2),
        },
        "notes": ["Must-pay = essential spending (housing, utilities, groceries, insurance, health, transportation) plus the "
                  "minimum payments on debts not already funded."],
    }
    if unknown_min:
        out["notes"].append("Minimum payment unknown for: " + ", ".join(unknown_min) + ". Treated as $0 here; add them for a true figure.")

    tl = income_timeline(profile, cash + pending, spending, today)
    if tl:
        out["income_timeline"] = tl

    # --- if income stops (e.g. a contract that does not convert); unemployment benefits are not assumed
    out["income_stops"] = [{
        "months_without_income": m, "cash_needed": round(must_pay * m, 2),
        "shortfall_today": round(max(0.0, must_pay * m - cash), 2),
        "shortfall_after_deposits": round(max(0.0, must_pay * m - cash - pending), 2),
    } for m in GAP_MONTHS]
    out["notes"].append("The stress test assumes no income and no unemployment benefits during the gap.")

    # --- trimming discretionary spending
    disc = {c: round(cat_avg[c], 2) for c in DISCRETIONARY if cat_avg.get(c)}
    base = goals.build_plan(goal_rows, accts, essential, surplus, today)
    base_idx = _ym_index(base["debt_free"])
    disc_total = sum(disc.values())
    trims = []
    for pct in TRIMS:
        freed = disc_total * pct / 100
        p = goals.build_plan(goal_rows, accts, essential, surplus + freed, today)
        idx = _ym_index(p["debt_free"])
        trims.append({"trim_pct": pct, "monthly_freed": round(freed, 2), "new_monthly_surplus": round(surplus + freed, 2),
                      "debt_free": p["debt_free"],
                      "months_sooner": (base_idx - idx) if base_idx and idx else None})
    out["discretionary"] = {"per_month": disc, "total_per_month": round(disc_total, 2),
                            "debt_free_today": base["debt_free"], "scenarios": trims}
    return out
