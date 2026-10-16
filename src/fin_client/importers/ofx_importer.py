"""OFX/QFX importer (offline; wraps ofxparse)."""
from decimal import Decimal
from pathlib import Path

from ofxparse import OfxParser

from fin_client.models import Transaction


def parse_ofx(path: Path, account: str | None = None) -> list[Transaction]:
    with open(path, "rb") as f:
        ofx = OfxParser.parse(f)
    out = []
    for acct in ofx.accounts:
        label = account or "default"
        for t in acct.statement.transactions:
            desc = (t.payee or t.memo or t.type or "").strip()
            out.append(Transaction(t.date.date(), desc, Decimal(str(t.amount)), label))
    return out
