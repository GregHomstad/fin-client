"""Render the advisor snapshot (aggregates only, no raw transactions) as a PDF. Fully offline."""
from datetime import date
from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
    c.categoryAxis.categoryNames = [m["month"][2:] for m in history]
    c.categoryAxis.labels.fontSize = 7
    c.valueAxis.labels.fontSize = 7
    c.categoryAxis.labels.fontName = c.valueAxis.labels.fontName = "Helvetica"
    c.valueAxis.valueMin = 0
    c.valueAxis.labelTextFormat = lambda v: f"${v:,.0f}"
    c.bars[0].fillColor, c.bars[1].fillColor = INCOME_C, SPEND_C
    c.bars.strokeColor = None
    d.add(c)
    d.add(String(50, 2.15 * inch, "Income", fontName="Helvetica", fontSize=8, fillColor=INCOME_C))
    d.add(String(95, 2.15 * inch, "Spending", fontName="Helvetica", fontSize=8, fillColor=SPEND_C))
    return d


def build_pdf(snap: dict, out: Path) -> Path:
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

    story += [Paragraph("Income vs spending by month", h2), _chart(snap["monthly_history"])]

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
