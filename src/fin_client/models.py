from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Transaction:
    date: date
    description: str
    amount: Decimal  # negative = money out
    account: str = "default"
    bank_category: str = ""  # category supplied by the bank export, if any
