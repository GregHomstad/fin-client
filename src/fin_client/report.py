"""Render the advisor snapshot (aggregates only, no raw transactions) as a PDF. Fully offline."""
from datetime import date
from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from fin_client.mdlite import to_flowables
from fin_client.paths import REPO_ROOT

INK, MUTED, ACCENT, GRID = colors.HexColor("#1f2937"), colors.HexColor("#6b7280"), colors.HexColor("#2563eb"), colors.HexColor("#e5e7eb")
INCOME_C, SPEND_C = colors.HexColor("#16a34a"), colors.HexColor("#dc2626")
DISCLAIMER = ("Educational estimates generated locally from your own data; not a licensed financial advisor and not "
              "tax, legal or investment advice. Tax figures are federal + Arkansas only and exclude credits, capital "
              "gains, AMT and other items. Verify with a professional before acting.")


def money(x, dec=0) -> str:
    if x is None:
        return "-"
    return f"-${abs(x):,.{dec}f}" if x < 0 else f"${x:,.{dec}f}"


def check_output_path(out: Path) -> Path:
    out = out.resolve()
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        raise RuntimeError(f"Refusing to write a financial report inside the repo: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _table(rows, col_widths=None, align_right_from=1):
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID), ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _chart(history):
    d = Drawing(6.5 * inch, 2.3 * inch)
    c = VerticalBarChart()
    c.x, c.y, c.width, c.height = 45, 25, 6.5 * inch - 60, 1.7 * inch
    c.data = [[m["income"] for m in history], [m["spending"] for m in history]]
    c.categoryAxis.categoryNames = [(m["month"][2:] + ("*" if m.get("missing") else "")) for m in history]
    c.categoryAxis.labels.fontSize = 6.5
    c.categoryAxis.labels.fontName = c.valueAxis.labels.fontName = "Helvetica"
    c.valueAxis.labels.fontSize = 7
    c.valueAxis.valueMin = 0
    c.valueAxis.labelTextFormat = lambda v: f"${v:,.0f}"
    c.bars[0].fillColor, c.bars[1].fillColor = INCOME_C, SPEND_C
    c.bars.strokeColor = None
    d.add(c)
    d.add(String(50, 2.15 * inch, "Income", fontName="Helvetica", fontSize=8, fillColor=INCOME_C))
    d.add(String(95, 2.15 * inch, "Spending", fontName="Helvetica", fontSize=8, fillColor=SPEND_C))
    return d


def build_pdf(snap: dict, out: Path, narrative: str | None = None, narrative_label: str = "") -> Path:
    out = check_output_path(out)
    ss = getSampleStyleSheet()
    h1, h2, body = ss["Title"], ss["Heading2"], ss["BodyText"]
    h2.textColor, h2.spaceBefore, h2.spaceAfter = ACCENT, 12, 4
    small = ss["BodyText"].clone("small", fontSize=7.5, textColor=MUTED, leading=10)
    story = []

    p, avg = snap["period"], snap["monthly_averages"]
    story += [Paragraph("Personal Finance Report", h1),
              Paragraph(f"Generated {date.today():%B %d, %Y} &middot; data {p['first_month']} to {p['last_month']} "
                        f"({p['months_in_averages']} full months in averages)", small), Spacer(1, 8)]

    dq = snap.get("data_quality") or []
    if dq:
        story.append(Paragraph("Read this first: data quality", h2))
        for w in dq:
            story.append(Paragraph("&bull; " + w.replace("&", "&amp;"), body))
        story.append(Spacer(1, 6))
    if narrative:
        bullet = ss["BodyText"].clone("bullet", leftIndent=12, firstLineIndent=-8, spaceAfter=2)
        h3 = ss["Heading3"]
        story.append(Paragraph("Victor's wealth plan", h2))
        story.append(Paragraph(f"AI-written narrative{' (' + narrative_label + ')' if narrative_label else ''}. The tables that follow "
                               "are computed by code from your own data: check every figure here against them.", small))
        story += to_flowables(narrative, {"h2": h2, "h3": h3, "body": body, "bullet": bullet})
        story.append(PageBreak())
        story.append(Paragraph("The numbers behind the plan", h1))

    rate = f"{avg['savings_rate_pct']:.1f}%" if avg.get("savings_rate_pct") is not None else "-"
    story.append(_table([["Monthly average", ""],
                         ["Income (bank deposits)", money(avg["income"])],
                         ["Spending", money(avg["spending"])],
                         ["Net", money(avg["net"])],
                         ["Savings rate", rate],
                         ["Essential spending / month", money(snap["essential_spending_per_month"])],
                         ["Recurring outflows / month",
                          f"{money(snap['recurring_outflows_per_month'])}"
                          + (f" ({snap['recurring_outflows_pct_of_income']:.0f}% of income)"
                             if snap.get("recurring_outflows_pct_of_income") is not None else "")]],
                        [3.2 * inch, 2.6 * inch]))

    hist = snap.get("history_window") or snap["monthly_history"]
    have = snap.get("months_with_data", len(snap["monthly_history"]))
    story += [Paragraph("Income vs spending by month (last 12 months)", h2), _chart(hist)]
    story.append(Paragraph(f"{have} of the last 12 months have data. Months marked * have no imported transactions; "
                           "import older statements to fill them in. Partial first and last months can look low.", small))

    story.append(Paragraph("Average monthly spending by category", h2))
    total = sum(snap["average_spending_by_category"].values()) or 1
    story.append(_table([["Category", "Per month", "Share"]] +
                        [[c, money(v), f"{v / total * 100:.0f}%"] for c, v in snap["average_spending_by_category"].items()],
                        [3.2 * inch, 1.4 * inch, 1.2 * inch]))

    if snap["recurring_items"]:
        story.append(Paragraph("Recurring charges and deposits", h2))
        story.append(_table([["Merchant", "Cadence", "Amount"]] +
                            [[r["merchant"].title(), r["cadence"], money(r["amount"], 2)] for r in snap["recurring_items"]],
                            [3.2 * inch, 1.4 * inch, 1.2 * inch]))

    plan = snap.get("planning", {})
    ef = plan.get("emergency_fund")
    if ef:
        story.append(Paragraph("Emergency fund", h2))
        rows = [["Target: 3 months of essentials", money(ef["target_3_months"])],
                ["Target: 6 months of essentials", money(ef["target_6_months"])]]
        if "cash_savings" in ef:
            rows += [["Current cash savings", money(ef["cash_savings"])],
                     ["Months covered", f"{ef['months_covered']}"],
                     ["Shortfall to 3 / 6 months", f"{money(ef['shortfall_to_3_months'])} / {money(ef['shortfall_to_6_months'])}"]]
        story.append(_table([["Item", "Amount"]] + rows, [3.2 * inch, 2.6 * inch]))

    dp = plan.get("debt_payoff")
    if dp:
        story.append(Paragraph(f"Debt payoff (total debt {money(dp['total_debt'])})", h2))
        story.append(_table([["Scenario", "Payment/mo", "Months", "Total interest"]] +
                            [[s["scenario"], money(s["monthly_payment_total"]),
                              s["months_to_debt_free"] if s["months_to_debt_free"] is not None else "never",
                              money(s["total_interest"]) if s["total_interest"] is not None else "-"]
                             for s in dp["scenarios"]],
                            [2.8 * inch, 1.1 * inch, 0.8 * inch, 1.2 * inch]))
        for a in dp.get("assumptions", []):
            story.append(Paragraph(f"Assumption: {a}", small))

    rt = plan.get("retirement")
    if rt and "scenarios" in rt:
        a = rt["assumptions"]
        story.append(Paragraph(f"Retirement projection ({rt['years_to_retirement']} years, today's dollars)", h2))
        has_t = "balance_needed" in rt["scenarios"][0]
        head = ["Scenario", "Return", "Projected balance", "Income/mo (4% rule)"] + (["On track?", "Needed contrib/mo"] if has_t else [])
        rows = [head]
        for s in rt["scenarios"]:
            r = [s["scenario"], f"{s['nominal_return_pct']:.1f}%", money(s["projected_balance"]), money(s["sustainable_monthly_income"])]
            if has_t:
                r += ["yes" if s["on_track"] else "no", money(s["monthly_contribution_needed"])]
            rows.append(r)
        story.append(_table(rows))
        story.append(Paragraph(f"Assumes {a['inflation_pct']}% inflation and a {a['safe_withdrawal_rate_pct']:.0f}% "
                               "withdrawal rate; contributions rise with inflation.", small))

    tx = plan.get("tax")
    if tx and "error" not in tx:
        story.append(Paragraph(f"Taxes ({tx['tax_year']}, {tx['filing_status']})", h2))
        rows = [["Gross income", money(tx["gross_income"])],
                ["Taxable income (federal)", money(tx["taxable_income"])],
                ["Federal income tax", money(tx["federal_income_tax"])],
                ["FICA (Social Security + Medicare)", money(tx["fica"]["total"])]]
        if tx.get("state"):
            rows.append([f"{tx['state']['state']} income tax", money(tx["state"]["income_tax"])])
        rows += [["Total taxes", f"{money(tx['total_taxes'])} ({tx['total_effective_rate_pct']:.1f}% of gross)"],
                 ["Marginal rate on next dollar (fed+state+payroll)", f"{tx['combined_marginal_rate_on_next_dollar_pct']:.1f}%"],
                 ["Estimated take-home / month", money(tx["estimated_monthly_take_home"])]]
        story.append(_table([["Item", "Amount"]] + rows, [3.6 * inch, 2.4 * inch]))
        if tx["what_if"]:
            story.append(Spacer(1, 6))
            story.append(_table([["What if you contribute more pre-tax", "Extra", "Tax saved", "Real cost"]] +
                                [[w["scenario"], money(w["extra_pre_tax_contribution"]), money(w["total_tax_saved"]),
                                  money(w["net_take_home_cost"])] for w in tx["what_if"]],
                                [2.8 * inch, 1.0 * inch, 1.0 * inch, 1.1 * inch]))
        for w in tx["warnings"]:
            story.append(Paragraph(f"Warning: {w}", small))

    gp = snap.get("goals_plan")
    if gp:
        story.append(Paragraph("Goals and action plan", h2))
        if gp["this_month"]:
            story.append(_table([["Next month's surplus goes to", "Amount"]] + [[a["bucket"], money(a["amount"])] for a in gp["this_month"]],
                                [3.6 * inch, 2.2 * inch]))
        if gp["goals"]:
            story.append(Spacer(1, 6))
            story.append(_table([["Goal", "Target", "Wanted by", "Projected", "Status"]] +
                                [[g["name"], money(g["target"]) if g.get("target") else "-", (g.get("target_date") or "-")[:7],
                                  g.get("eta") or "-", g.get("status", "")] for g in gp["goals"]],
                                [1.9 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch]))
        if gp["milestones"]:
            story.append(Spacer(1, 6))
            story.append(_table([["Milestone", "When"]] + [[m["label"], m["date"]] for m in gp["milestones"][:14]], [4.4 * inch, 1.4 * inch]))
        if gp.get("debt_free"):
            story.append(Paragraph(f"Debt free around {gp['debt_free']} at this pace.", small))
        for a in gp["assumptions"] + gp["warnings"]:
            story.append(Paragraph(f"Note: {a}", small))

    rc = snap.get("recovery")
    if rc:
        pos = rc["position"]
        story.append(Paragraph("Getting back on track: your numbers", h2))
        story.append(Paragraph("Computed scenarios from your own data, not advice. The choices are yours.", small))
        story.append(_table([["Where you stand", "Amount"],
                             ["Net worth", money(pos["net_worth"])],
                             ["Assets / liabilities", f"{money(pos['assets'])} / {money(pos['liabilities'])}"],
                             ["Debt not yet funded", money(pos["debt_not_yet_funded"])],
                             ["Set aside for debts already funded", money(pos["set_aside_for_debts"])],
                             ["Usable cash (excludes money set aside)", money(pos["usable_cash"])],
                             ["Must-pay costs per month (essentials + debt minimums)", money(pos["must_pay_per_month"])],
                             ["Months of must-pay costs your cash covers", f"{pos['months_covered_today']}" if pos["months_covered_today"] is not None else "-"],
                             ["Monthly surplus today", money(pos["monthly_surplus"])]],
                            [3.9 * inch, 2.0 * inch]))
        if pos["pending_deposits"]:
            story.append(Paragraph(f"Not counted yet: {money(pos['pending_deposits'])} waiting to be deposited (would cover "
                                   f"{pos['months_covered_after_deposits']} months).", small))
        story.append(Spacer(1, 6))
        story.append(Paragraph("If income stops (for example, a contract that does not convert)", h2))
        story.append(_table([["Months without income", "Cash needed", "Short today", "Short after deposits"]] +
                            [[str(r["months_without_income"]), money(r["cash_needed"]), money(r["shortfall_today"]),
                              money(r["shortfall_after_deposits"])] for r in rc["income_stops"]],
                            [1.8 * inch, 1.3 * inch, 1.3 * inch, 1.5 * inch]))
        disc = rc["discretionary"]
        if disc["per_month"]:
            story.append(Paragraph("What trimming discretionary spending would change", h2))
            story.append(_table([["Discretionary category", "Per month"]] + [[c, money(v)] for c, v in disc["per_month"].items()]
                                + [["Total", money(disc["total_per_month"])]], [3.9 * inch, 2.0 * inch]))
            story.append(Spacer(1, 4))
            story.append(_table([["Trim", "Freed / month", "New surplus", "Debt free", "Months sooner"]] +
                                [[f"{t['trim_pct']}%", money(t["monthly_freed"]), money(t["new_monthly_surplus"]),
                                  t["debt_free"] or "-", str(t["months_sooner"]) if t["months_sooner"] is not None else "-"]
                                 for t in disc["scenarios"]], [0.8 * inch, 1.3 * inch, 1.3 * inch, 1.2 * inch, 1.3 * inch]))
            story.append(Paragraph(f"Debt free around {disc['debt_free_today'] or 'not within 10 years'} at today's pace.", small))
        for n in rc["notes"]:
            story.append(Paragraph(f"Note: {n}", small))

    rec = snap.get("paycheck_reconciliation")
    if rec:
        story.append(Paragraph("Paycheck check", h2))
        story.append(Paragraph(f"Estimated take-home {money(rec['estimated_monthly_take_home'])}/mo vs bank deposits "
                               f"{money(rec['bank_income_per_month'])}/mo (difference {money(rec['difference'])}). "
                               f"{rec['note']}", body))

    if plan.get("missing_inputs"):
        story.append(Paragraph("Not computed (missing inputs in profile.json)", h2))
        for m in plan["missing_inputs"]:
            story.append(Paragraph(f"&bull; {m}", small))

    story += [Spacer(1, 14), Paragraph(DISCLAIMER, small)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.5 * inch, "CONFIDENTIAL - personal financial data. Generated locally by fin-client.")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                      topMargin=0.7 * inch, bottomMargin=0.8 * inch, title="Personal Finance Report",
                      author="fin-client").build(story, onFirstPage=footer, onLaterPages=footer)
    return out


def build_transactions_pdf(txns, rules, cov: dict, out: Path) -> Path:
    """Every transaction with its category, plus a data-coverage page that explains gaps."""
    from fin_client.categorize import categorize
    out = check_output_path(out)
    ss = getSampleStyleSheet()
    h1, h2, body = ss["Title"], ss["Heading2"], ss["BodyText"]
    h2.textColor = ACCENT
    small = ss["BodyText"].clone("small2", fontSize=7.5, textColor=MUTED, leading=10)
    story = [Paragraph("All transactions and data coverage", h1),
             Paragraph(f"Generated {date.today():%B %d, %Y}. {len(txns)} transactions, {cov.get('oldest', '-')} to {cov.get('newest', '-')}.", small),
             Spacer(1, 6), Paragraph("Why the totals may not look real", h2)]
    for w in cov["warnings"]:
        story.append(Paragraph("&bull; " + w.replace("&", "&amp;"), body))
    story += [Spacer(1, 6), Paragraph("By month (as imported)", h2),
              _table([["Month", "Txns", "Active days", "Money in", "Money out"]] +
                     [[m["month"], m["transactions"], f"{m['first_day']}-{m['last_day']} of {m['days_in_month']}" + (" (partial)" if m["looks_partial"] else ""),
                       money(m["money_in"], 2), money(m["money_out"], 2)] for m in cov["months"]],
                     [0.9 * inch, 0.7 * inch, 2.0 * inch, 1.2 * inch, 1.2 * inch]),
              Spacer(1, 6), Paragraph("By account", h2),
              _table([["Account", "Txns", "First", "Last"]] + [[a["name"], a["transactions"], a["first"] or "-", a["last"] or "none imported"] for a in cov["accounts"]],
                     [2.6 * inch, 0.8 * inch, 1.3 * inch, 1.3 * inch]),
              PageBreak(), Paragraph("Every transaction", h2)]
    rows = [["Date", "Merchant", "Account", "Category", "Amount"]]
    for t in sorted(txns, key=lambda x: x.date, reverse=True):
        cat = categorize(t.description, t.amount, rules, t.bank_category)
        rows.append([t.date.isoformat(), " ".join(t.description.split())[:34], t.account[:16], cat[:18], money(float(t.amount), 2)])
    tbl = _table(rows, [0.8 * inch, 2.3 * inch, 1.1 * inch, 1.2 * inch, 0.9 * inch])
    tbl.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("ALIGN", (4, 0), (4, -1), "RIGHT"), ("ALIGN", (1, 0), (3, -1), "LEFT")]))
    story.append(tbl)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.5 * inch, "CONFIDENTIAL - personal financial data. Generated locally by fin-client.")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch,
                      bottomMargin=0.8 * inch, title="All transactions", author="fin-client").build(story, onFirstPage=footer, onLaterPages=footer)
    return out
