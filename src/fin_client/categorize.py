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


BANK_CATEGORY_KEY = "_bank_categories"  # rules.json: {"_bank_categories": {"bank label": "Our Category"}}
USER_KEY = "_user"  # user keyword rules live here so they outrank the bank's label

# Bank labels are looked up whole, then by their first segment ("Food/Alcohol" -> "food")
BANK_DEFAULT_MAP = {
    "food/groceries": "Groceries", "food/dining out": "Dining", "food/alcohol": "Dining", "food": "Groceries",
    "transportation": "Transportation", "utilities": "Utilities", "housing": "Housing", "income": "Income",
    "insurance": "Insurance", "healthcare": "Health", "shopping": "Shopping", "entertainment": "Entertainment",
    "financial services/atm": "Cash & ATM", "miscellaneous": "Other",
}


def _bank_category(bank_cat: str, overrides: dict[str, str]) -> str | None:
    """Map the bank's own category label to ours; unknown labels are kept as-is."""
    name = bank_cat.strip()
    low = name.lower()
    if not name or low.startswith("uncategorized") or low in ("other", "unknown"):
        return None
    if low in overrides:
        return overrides[low]
    if "transfer" in low:
        return "Transfers"
    if low in BANK_DEFAULT_MAP:
        return BANK_DEFAULT_MAP[low]
    first = low.split("/")[0].split(" - ")[0].strip()
    return overrides.get(first) or BANK_DEFAULT_MAP.get(first) or name


def load_rules() -> Rules:
    """Built-in keyword rules plus your rules.json (keywords, and optional bank-label overrides)."""
    path = data_dir() / "rules.json"
    merged: dict = {cat: list(kws) for cat, kws in DEFAULT_RULES.items()}
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        merged[BANK_CATEGORY_KEY] = {k.lower(): v for k, v in user.pop(BANK_CATEGORY_KEY, {}).items()}
        merged[USER_KEY] = {k: [s.upper() for s in v] for k, v in user.items()}
    return merged


def categorize(description: str, amount: Decimal, rules: Rules | None = None, bank_category: str = "") -> str:
    """Priority: your keyword rules, the bank's own category, built-in keywords, then a fallback."""
    rules = DEFAULT_RULES if rules is None else rules
    d = description.upper()
    for cat, kws in rules.get(USER_KEY, {}).items():  # type: ignore[union-attr]
        if any(k in d for k in kws):
            return cat
    mapped = _bank_category(bank_category, rules.get(BANK_CATEGORY_KEY, {}))  # type: ignore[arg-type]
    if mapped:
        return mapped
    for cat, kws in rules.items():
        if not cat.startswith("_") and any(k in d for k in kws):
            return cat
    return "Income" if amount > 0 else "Uncategorized"
