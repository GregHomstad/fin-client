# Privacy model

This repo is public. Your bank data must never be.

## Rules
1. **Data lives outside the repo.** Default: `%USERPROFILE%\.fin-client` (Windows; not AppData, which Windows redirects per packaged app) or `~/.local/share/fin-client`. The code refuses to use a data directory inside the repo.
2. **Advisor is local by default.** Ollama on your machine; nothing goes to a cloud API. **Claude (cloud) is opt-in** (`fin-client advise --claude`, or the Claude button on the Advisor page): it never runs without your approval. Before every send you see the complete request; only aggregated totals and plan results are included (never individual transactions), account and debt names become "Debt 1..n", and recurring merchant names are hidden unless you tick the box. The dashboard refuses to send anything but the exact payload you previewed (fingerprint check). The API key comes only from the environment (`ANTHROPIC_API_KEY`); it is never stored by this app.
3. **Claude Code never sees real data.** Claude Code (cloud) is used only to develop this tool against synthetic fixtures in `samples/`. `.claude/settings.json` denies reading the data directory. Do not paste statements into a cloud chat.
4. **Commit guard.** `.githooks/pre-commit` blocks commits containing data-file extensions (outside `samples/`) or account-number-like strings. Enable with `git config core.hooksPath .githooks`.
5. **Fixtures are synthetic.** Only `samples/synthetic_*` CSV/OFX files may be committed, and they must contain invented data.
6. **No telemetry, no network calls** in the import/analysis path. Only the local Ollama endpoint (`localhost:11434`) is contacted.

## If you leak something
Rotate account numbers/cards with your bank, then purge git history (`git filter-repo`) and force-push; treat the data as exposed anyway, since public repos are cloned and cached quickly.
