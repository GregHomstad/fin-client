"""Rule-based categorization. User rules (data dir) take priority over defaults."""
import json
from decimal import Decimal

from fin_client.paths import data_dir

Rules = dict[str, list[str]]

DEFAULT_RULES: Rules = {
    "Transfers": ["TRANSFER", "ZELLE", "VENMO", "PAYMENT THANK YOU", "AUTOPAY"],
    "Income": ["PAYROLL", "DIRECT DEP", "SALARY", "INTEREST PAID", "REFUND"],
    "Housing": ["RENT", "MORTGAGE", "APARTMENT", "HOA"],
    "Utilities": ["ELECTRIC", "WATER", "GAS CO", "INTERNET", "COMCAST", "AT&T", "VERIZON", "T-MOBILE"],
    "Groceries": ["GROCERY", "WALMART", "KROGER", "ALDI", "TRADER JOE", "WHOLE FOODS", "COSTCO"],
    "Dining": ["COFFEE", "RESTAURANT", "CAFE", "PIZZA", "MCDONALD", "STARBUCKS", "DOORDASH", "UBER EATS"],
    "Transportation": ["UBER", "LYFT", "SHELL", "EXXON", "CHEVRON", "FUEL", "PARKING"],
    "Subscriptions": ["STREAMING", "NETFLIX", "SPOTIFY", "HULU", "DISNEY", "SUBSCRIPTION"],
    "Health": ["PHARMACY", "CVS", "WALGREENS", "DENTAL", "CLINIC", "MEDICAL"],
    "Insurance": ["INSURANCE", "GEICO", "STATE FARM"],
    "Shopping": ["AMAZON", "TARGET", "BEST BUY"],
}


def load_rules() -> Rules:
    """Merge user rules.json (if any) ahead of defaults."""
    path = data_dir() / "rules.json"
    merged: Rules = {}
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        merged.update({k: [s.upper() for s in v] for k, v in user.items()})
    for cat, kws in DEFAULT_RULES.items():
        merged[cat] = merged.get(cat, []) + kws
    return merged


def categorize(description: str, amount: Decimal, rules: Rules | None = None) -> str:
    rules = DEFAULT_RULES if rules is None else rules
    d = description.upper()
    for cat, kws in rules.items():
        if any(k in d for k in kws):
            return cat
    return "Income" if amount > 0 else "Uncategorized"
