# fin-client

A local-first, offline personal finance advisor client. Import your own bank exports (CSV, OFX/QFX, later PDF), analyze them on your machine, and get budgeting, investing, debt, retirement and tax guidance from a local Ollama model.

> Educational tool, not a licensed financial advisor. Not professional financial, tax or legal advice.

## Privacy first
This repo is public, so real data never lives here. See [PRIVACY.md](PRIVACY.md): data directory outside the repo, offline LLM only, commit-guard hook, synthetic fixtures only.

Enable the guard after cloning:
```bash
git config core.hooksPath .githooks
```

## Roadmap
1. Importers: CSV (generic mapping), OFX/QFX, PDF
2. Local store + categorization rules
3. Analytics: cash flow, recurring charges, net worth, debt payoff
4. Planning: PyPortfolioOpt allocation, retirement projections
5. Advisor chat via Ollama, grounded in computed numbers (not raw rows)
6. Claude Code skill for developing/running the CLI (never reads real data)

## Usage
```bash
fin-client import statement.csv      # or .ofx / .qfx
fin-client cashflow | categories | recurring
fin-client plan                      # debt payoff, emergency fund, retirement (pure math, no model)
fin-client tax                       # federal tax estimate + 401(k)/IRA what-ifs (profile.json only)
fin-client advise --show-context     # exactly what the model would see; no model call
fin-client advise [--model llama3:8b]
```
The advisor sees only computed aggregates (category averages, savings rate, recurring commitments), never raw transactions. Add facts it can't infer (age, debts with APRs, balances, goals) to `profile.json` in your data directory (`fin-client where`). 

Example `profile.json` (all fields optional; missing ones are reported, not guessed):
```json
{"age": 30, "retire_age": 65, "cash_savings": 1500, "retirement_balance": 20000,
 "monthly_retirement_contribution": 300, "target_retirement_spending_monthly": 4000,
 "expected_return_pct": 6, "inflation_pct": 2.5,
 "filing_status": "single", "annual_gross_income": 100000, "traditional_401k_annual": 6000,
 "debts": [{"name": "card", "balance": 3000, "apr_pct": 24, "min_payment": 90}]}
```
Tax estimates (2026 tables, IRS Rev. Proc. 2025-32 and Notice 2025-67) cover federal ordinary income tax, standard/itemized deduction, 401(k) catch-ups, traditional IRA deduction and Roth IRA phase-outs. They include employee FICA and Arkansas income tax (2026: 3.7% top rate, DFA formula; set `"state": "none"` to skip). They exclude other states, credits, capital gains, AMT and HSAs. The snapshot compares estimated take-home to your actual bank deposits. Gross income must come from you, since bank deposits are after-tax. Add a new year by extending `tax.DATA`.

Planning results are computed in code and given to the model as facts, in today's dollars. It refuses any non-loopback `OLLAMA_HOST`.

## Develop
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
```
