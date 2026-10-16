from pathlib import Path

from fin_client.importers.csv_importer import parse_csv
from fin_client.importers.ofx_importer import parse_ofx
from fin_client.models import Transaction


def import_file(path: Path, account: str = "default") -> list[Transaction]:
    ext = path.suffix.lower()
    if ext == ".csv":
        return parse_csv(path, account)
    if ext in (".ofx", ".qfx"):
        return parse_ofx(path, account)
    raise ValueError(f"Unsupported format: {ext} (supported: .csv, .ofx, .qfx)")
