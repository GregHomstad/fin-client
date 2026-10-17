"""Interactive local labeling of uncategorized merchants. Saves keyword rules to rules.json (data dir)."""
import json
import re
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal

from fin_client.categorize import USER_KEY, categorize, load_rules
from fin_client.models import Transaction
from fin_client.paths import data_dir

STANDARD = ["Housing", "Utilities", "Groceries", "Dining", "Transportation", "Subscriptions", "Health", "Insurance",
            "Shopping", "Entertainment", "Cash & ATM", "Debt Payments", "Savings", "Transfers", "Income", "Other"]


def default_keyword(description: str) -> str:
    """Leading text up to the first digit or '#', which usually drops store numbers and dates."""
    return re.split(r"[\d#*]", description.upper())[0].strip(" -*#") or description.upper().strip()


def uncategorized_merchants(txns: list[Transaction], rules) -> list[tuple[str, int, Decimal]]:
    groups: dict[str, list[Decimal]] = defaultdict(list)
    for t in txns:
        if categorize(t.description, t.amount, rules, t.bank_category) in ("Uncategorized",):
            groups[t.description].append(t.amount)
    rows = [(d, len(v), sum((abs(a) for a in v), Decimal(0))) for d, v in groups.items()]
    return sorted(rows, key=lambda r: -r[2])


def save_rule(keyword: str, category: str) -> None:
    path = data_dir() / "rules.json"
    rules = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    kws = rules.setdefault(category, [])
    if keyword.upper() not in (k.upper() for k in kws):
        kws.append(keyword.upper())
    path.write_text(json.dumps(rules, indent=1), encoding="utf-8")


def run(txns: list[Transaction], ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> int:
    """Walk through uncategorized merchants, biggest first. Returns the number of rules saved."""
    saved = 0
    queue = uncategorized_merchants(txns, load_rules())
    if not queue:
        say("Nothing uncategorized.")
        return 0
    say(f"{len(queue)} uncategorized merchants, largest total first. Enter = skip, q = quit, ? = category list.")
    for desc, n, total in queue:
        while True:
            say(f"\n{desc}   ({n} txn{'s' if n != 1 else ''}, ${total:,.2f} total)")
            cat = ask("  category> ").strip()
            if cat == "?":
                say("  " + ", ".join(STANDARD) + "  (or type your own)")
                continue
            break
        if cat.lower() == "q":
            break
        if not cat:
            continue
        if cat.isdigit() and 1 <= int(cat) <= len(STANDARD):
            cat = STANDARD[int(cat) - 1]
        kw = ask(f"  keyword to match [{default_keyword(desc)}]> ").strip() or default_keyword(desc)
        if kw.upper() not in desc.upper():
            say("  (keyword must appear in the merchant text; skipped)")
            continue
        save_rule(kw, cat)
        saved += 1
        say(f"  saved: '{kw.upper()}' -> {cat}")
    say(f"\nSaved {saved} rule(s). Re-run `fin-client categories` to review.")
    return saved
