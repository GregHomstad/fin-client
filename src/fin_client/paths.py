"""Locate the private data directory, always outside the repository."""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("FIN_CLIENT_DATA")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"]) / "fin-client"
    else:
        base = Path.home() / ".local" / "share" / "fin-client"
    base = base.resolve()
    if base == REPO_ROOT or REPO_ROOT in base.parents:
        raise RuntimeError(f"Refusing to store financial data inside the repo: {base}")
    base.mkdir(parents=True, exist_ok=True)
    return base
