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


def build(surplus: float, essential: float, cat_avg: dict[str, float], accts: list[dict], goal_rows: list[dict],
          profile: dict, today: date | None = None) -> dict:
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
