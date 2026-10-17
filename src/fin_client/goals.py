"""Goals and a month-by-month action plan, computed locally from your accounts and cash flow.

Waterfall (a common educational order, not advice): starter emergency fund -> debts above 8% APR ->
emergency fund target -> your dated goals -> remaining debts -> investing. Minimum debt payments are assumed
to be inside your current spending; once a debt is paid off its minimum is freed and rolls forward.
"""
import sqlite3
from datetime import date

KINDS = {"emergency_fund": "Emergency fund", "debt_free": "Debt free", "savings": "Savings goal", "invest": "Investing"}
HIGH_APR = 8.0
STARTER_MONTHS = 1.0
HORIZON = 120  # months


def init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL,
            target REAL, target_date TEXT, current REAL, months REAL, monthly REAL,
            priority INTEGER NOT NULL DEFAULT 50, notes TEXT NOT NULL DEFAULT '')"""
    )


def _f(x):
    if x in (None, ""):
        return None
    try:
        return float(str(x).replace("$", "").replace(",", ""))
    except ValueError as e:
        raise ValueError(f"Not a number: {x!r}") from e


def add_goal(conn, kind: str, name: str, target=None, target_date=None, current=None, months=None, monthly=None,
             priority: int = 50, notes: str = "") -> int:
    init(conn)
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    name = (name or "").strip() or KINDS[kind]
    t, c, m, mo = _f(target), _f(current), _f(months), _f(monthly)
    if kind == "savings" and not t:
        raise ValueError("A savings goal needs a target amount")
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError as e:
            raise ValueError("target_date must look like 2027-09-30") from e
    cur = conn.execute("INSERT INTO goals (kind,name,target,target_date,current,months,monthly,priority,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                       (kind, name, t, target_date or None, c, m, mo, int(priority), notes))
    conn.commit()
    return cur.lastrowid


def delete_goal(conn, goal_id: int) -> None:
    init(conn)
    conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
    conn.commit()


def list_goals(conn) -> list[dict]:
    init(conn)
    cols = ["id", "kind", "name", "target", "target_date", "current", "months", "monthly", "priority", "notes"]
    return [dict(zip(cols, r)) | {"kind_label": KINDS[r[1]]} for r in conn.execute(
        "SELECT id,kind,name,target,target_date,current,months,monthly,priority,notes FROM goals ORDER BY priority, id")]


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def build_plan(goals: list[dict], accts: list[dict], essential_monthly: float, surplus: float,
               today: date | None = None) -> dict:
    today = today or date.today()
    start = _add_months(today, 1)
    warnings: list[str] = []
    assumptions = ["Current minimum debt payments are assumed to already be part of your spending; paid-off debts free "
                   "their minimum, which then rolls forward."]
    S = max(0.0, surplus)
    if surplus <= 0:
        warnings.append("Your spending is at or above your income, so there is no surplus to allocate. Fixing that comes first.")

    cash = sum(a["balance"] for a in accts if a["type"] in ("checking", "savings", "cash") and a["balance"] is not None)
    if not any(a["type"] in ("checking", "savings", "cash") and a["balance"] is not None for a in accts):
        warnings.append("No cash balances entered, so the emergency fund is assumed to start at $0.")

    # ---- debts from liability accounts
    debts = []
    for a in accts:
        if a["kind"] == "liability" and a["balance"] and a["balance"] > 0:
            apr = a["apr_pct"]
            if apr is None:
                assumptions.append(f"{a['name']}: APR unknown, assumed 0%. Add it for a realistic plan.")
                apr = 0.0
            mn = a["min_payment"]
            if mn is None:
                mn = max(25.0, a["balance"] * 0.02)
                assumptions.append(f"{a['name']}: minimum payment unknown, assumed ${mn:,.0f}.")
            debts.append({"name": a["name"], "bal": float(a["balance"]), "apr": float(apr), "min": float(mn), "done": None})

    # ---- goal buckets
    buckets = []  # (phase, order, dict)
    ef_goals = [g for g in goals if g["kind"] == "emergency_fund"]
    starter_target = essential_monthly * STARTER_MONTHS
    ef = {"name": "Starter emergency fund", "target": starter_target, "bal": cash, "start": cash, "date": None, "done": None,
          "id": None, "kind": "emergency_fund"}
    ef_full = None
    if ef_goals:
        g = ef_goals[0]
        tgt = g["target"] if g["target"] else essential_monthly * (g["months"] or 3.0)
        ef_full = {"name": g["name"], "target": tgt, "date": g["target_date"], "id": g["id"]}
    else:
        ef_full = {"name": "Emergency fund (3 months)", "target": essential_monthly * 3, "date": None, "id": None}
    sav = [{"name": g["name"], "target": g["target"], "bal": g["current"] or 0.0, "start": g["current"] or 0.0,
            "date": g["target_date"], "id": g["id"], "done": None, "prio": g["priority"], "kind": "savings"}
           for g in goals if g["kind"] == "savings"]
    sav.sort(key=lambda b: (b["date"] or "9999", b["prio"]))
    invest_cap = sum(g["monthly"] or 0 for g in goals if g["kind"] == "invest")
    invest_total = 0.0

    if essential_monthly <= 0:
        warnings.append("Essential spending is zero, so emergency-fund targets cannot be computed yet.")

    ef_main = {"name": ef_full["name"], "target": ef_full["target"], "id": ef_full["id"], "date": ef_full["date"], "done": None}
    timeline, milestones = [], []
    this_month: list[dict] = []
    freed = 0.0

    def note(label, d):
        milestones.append({"date": d.isoformat()[:7], "label": label})

    for m in range(HORIZON):
        d = _add_months(start, m)
        avail = S + freed
        alloc: dict[str, float] = {}

        # interest and minimums
        for x in debts:
            if x["bal"] > 0:
                x["bal"] += x["bal"] * x["apr"] / 1200
                pay = min(x["min"], x["bal"])
                x["bal"] -= pay
                if x["bal"] <= 0.005 and x["done"] is None:
                    x["bal"], x["done"] = 0.0, d
                    freed += x["min"]
                    note(f"{x['name']} paid off", d)

        def put(label, amt):
            nonlocal avail
            amt = min(amt, avail)
            if amt > 0.004:
                alloc[label] = alloc.get(label, 0) + amt
                avail -= amt
            return amt

        # 1 starter emergency fund
        if ef["bal"] >= starter_target - 0.005 and ef["done"] is None:
            ef["done"] = d
        if ef["bal"] < starter_target:
            ef["bal"] += put("Starter emergency fund", starter_target - ef["bal"])
            if ef["bal"] >= starter_target - 0.005 and ef["done"] is None:
                ef["done"] = d
                note("Starter emergency fund reached", d)
        # 2 high-APR debts
        for x in sorted([x for x in debts if x["apr"] >= HIGH_APR and x["bal"] > 0], key=lambda x: -x["apr"]):
            paid = put(f"Extra to {x['name']}", x["bal"])
            x["bal"] -= paid
            if x["bal"] <= 0.005 and x["done"] is None:
                x["bal"], x["done"] = 0.0, d
                freed += x["min"]
                note(f"{x['name']} paid off", d)
        # 3 full emergency fund
        ef_bal = max(ef["bal"], 0)
        if ef_bal >= ef_main["target"] - 0.005 and ef_main["done"] is None:
            ef_main["done"] = d  # already funded (no milestone to announce)
        if ef_bal < ef_main["target"]:
            ef["bal"] += put(ef_main["name"], ef_main["target"] - ef_bal)
            if ef["bal"] >= ef_main["target"] - 0.005 and ef_main["done"] is None:
                ef_main["done"] = d
                note(f"{ef_main['name']} reached", d)
        # 4 dated / prioritized savings goals
        for b in sav:
            if b["bal"] < b["target"]:
                b["bal"] += put(b["name"], b["target"] - b["bal"])
                if b["bal"] >= b["target"] - 0.005 and b["done"] is None:
                    b["done"] = d
                    note(f"{b['name']} reached", d)
        # 5 remaining debts
        for x in sorted([x for x in debts if x["apr"] < HIGH_APR and x["bal"] > 0], key=lambda x: -x["apr"]):
            paid = put(f"Extra to {x['name']}", x["bal"])
            x["bal"] -= paid
            if x["bal"] <= 0.005 and x["done"] is None:
                x["bal"], x["done"] = 0.0, d
                freed += x["min"]
                note(f"{x['name']} paid off", d)
        # 6 investing with whatever remains
        if avail > 0.004:
            amt = min(avail, invest_cap) if invest_cap else avail
            put("Investing / retirement", amt)
            invest_total += alloc.get("Investing / retirement", 0)

        if m == 0:
            this_month = [{"bucket": k, "amount": round(v, 2)} for k, v in alloc.items()]
        timeline.append({"month": d.isoformat()[:7], "debt": round(sum(x["bal"] for x in debts), 2),
                         "emergency_fund": round(ef["bal"], 2), "savings": round(sum(b["bal"] for b in sav), 2),
                         "invested": round(invest_total, 2)})
        debts_left = any(x["bal"] > 0 for x in debts)
        if not debts_left and m >= 24 and (ef_main["done"] or ef_full["target"] <= 0) and all(b["done"] for b in sav):
            break

    out_goals = []
    for g in goals:
        row = {"id": g["id"], "name": g["name"], "kind": g["kind"], "kind_label": g["kind_label"], "target": g["target"],
               "target_date": g["target_date"], "priority": g["priority"]}
        if g["kind"] == "savings":
            b = next(b for b in sav if b["id"] == g["id"])
            row.update(current=b["start"], eta=b["done"].isoformat()[:7] if b["done"] else None)
            row.update(_status(b["start"], g["target"], g["target_date"], b["done"], start))
        elif g["kind"] == "emergency_fund":
            row.update(current=cash, target=ef_main["target"], eta=ef_main["done"].isoformat()[:7] if ef_main["done"] else None)
            row.update(_status(cash, ef_main["target"], g["target_date"], ef_main["done"], start))
        elif g["kind"] == "debt_free":
            last = max((x["done"] for x in debts if x["done"]), default=None)
            all_done = bool(debts) and all(x["done"] for x in debts)
            row.update(current=None, target=sum(a["balance"] for a in accts if a["kind"] == "liability" and a["balance"]),
                       eta=last.isoformat()[:7] if all_done else None)
            if not debts:
                row.update(status="no debts to pay off")
            else:
                row.update(_status(None, None, g["target_date"], last if all_done else None, start, debt=True))
        else:
            row.update(current=None, eta=None, status="ongoing",
                       note=f"${invest_total / max(len(timeline), 1):,.0f}/month on average from leftover surplus")
        out_goals.append(row)

    debt_free = None
    if debts and all(x["done"] for x in debts):
        debt_free = max(x["done"] for x in debts).isoformat()[:7]
    elif debts:
        warnings.append(f"Debts are not fully paid off within {HORIZON // 12} years at this pace.")

    return {"as_of": today.isoformat(), "monthly_surplus": round(surplus, 2), "essential_monthly": round(essential_monthly, 2),
            "cash_on_hand": round(cash, 2), "this_month": this_month, "milestones": sorted(milestones, key=lambda x: x["date"]),
            "goals": out_goals, "debt_free": debt_free,
            "debts": [{"name": x["name"], "apr": x["apr"], "min": round(x["min"], 2), "paid_off": x["done"].isoformat()[:7] if x["done"] else None}
                      for x in debts],
            "timeline": timeline, "assumptions": assumptions, "warnings": warnings,
            "order": ["Starter emergency fund (1 month of essentials)", "Debts above 8% APR, highest first",
                      "Emergency fund target", "Your savings goals by date, then priority", "Remaining debts", "Investing"]}


def _status(current, target, target_date, done, start: date, debt: bool = False) -> dict:
    out: dict = {}
    if done is None:
        out["status"] = "not reached"
        if target_date:
            out["status"] = "behind"
        return out
    if not target_date:
        out["status"] = "on track"
        return out
    td = date.fromisoformat(target_date)
    out["status"] = "on track" if (done.year, done.month) <= (td.year, td.month) else "behind"
    if out["status"] == "behind":
        out["months_late"] = _months_between(td.replace(day=1), done)
    if not debt and target:
        left = max(0.0, target - (current or 0.0))
        months = max(1, _months_between(start, td.replace(day=1)) + 1)
        out["required_monthly"] = round(left / months, 2)
    return out
