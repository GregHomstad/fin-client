import pytest

from fin_client import advisor


@pytest.fixture(autouse=True)
def no_real_claude_cli(monkeypatch):
    """Tests must never launch the real `claude` CLI or an API call. Tests that need it re-patch shutil.which."""
    monkeypatch.setattr(advisor.shutil, "which", lambda name: None)
