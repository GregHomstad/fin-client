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

## Develop
```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
```
