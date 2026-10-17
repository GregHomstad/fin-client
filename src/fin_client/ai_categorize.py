"""AI merchant categorization: one decision per merchant, learned from your own labels, measured against them.

Pipeline: group transactions by merchant -> batch the unlabeled merchants -> ask a model (local Ollama by default,
or Claude after you approve the exact request) -> store labels with a confidence -> confident labels apply
automatically, the rest wait in a review queue. Your corrections become few-shot examples for the next run, and
`evaluate` scores the AI against the merchants you have labeled so you can see how good it really is.
"""
import json
import re
import statistics
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable

from fin_client import advisor, labels
from fin_client.categorize import LABELS_KEY, categorize
from fin_client.merchants import normalize
from fin_client.models import Transaction
from fin_client.transfers import MARK

TAXONOMY = {
    "Housing": "rent, mortgage, HOA, home repair and household supplies",
    "Utilities": "electric, water, gas, trash, sewer",
    "Internet & Phone": "internet, cell phone, cable",
    "Groceries": "supermarkets and grocery stores, warehouse clubs for food",
    "Dining": "restaurants, fast food, coffee shops, bars, food delivery",
    "Transportation": "fuel, rideshare, parking, tolls, public transit",
    "Auto": "car repairs, maintenance, registration, car wash, auto parts",
    "Insurance": "auto, home, renters, life, health insurance premiums",
    "Health": "doctors, dentists, pharmacy, vision, HSA spending",
    "Subscriptions": "streaming, apps, memberships and recurring digital services",
    "Entertainment": "movies, games, events, hobbies, books",
    "Shopping": "general retail, online marketplaces, clothing, electronics",
    "Fitness & Personal Care": "gym, salon, barber, spa, cosmetics",
    "Education": "tuition, courses, school fees, student supplies",
    "Gifts & Donations": "charity, gifts, church, crowdfunding",
    "Travel": "flights, hotels, car rentals, vacations",
    "Pets": "vet, pet food and supplies",
    "Kids & Family": "childcare, kids' items, family expenses",
    "Fees & Interest": "bank fees, interest charged, late fees, overdraft, annual fees",
    "Debt Payments": "payments to lenders, loan servicers, credit card issuers, collections",
    "Cash & ATM": "ATM withdrawals, cash back",
    "Transfers": "moving money between the person's own accounts, peer-to-peer to oneself",
    "Savings & Investing": "deposits to savings, brokerage, retirement accounts",
    "Income": "paychecks, direct deposits, refunds, interest earned, reimbursements",
    "Taxes": "income tax, property tax, vehicle tax, tax preparers",
    "Other": "only when nothing above fits",
}
_BY_LOWER = {k.lower(): k for k in TAXONOMY}

SYSTEM = """You categorize bank and credit-card merchants for one person's personal finances.
Choose exactly ONE category per merchant from this list (name: what belongs there):
{taxonomy}

Rules:
- Use the merchant text, whether money went out or came in (direction), typical amount, the account type and the
  bank's own label (a hint, sometimes wrong) to decide.
- Money IN that is a paycheck, refund or deposit is Income. Payments to lenders or card issuers are Debt Payments.
  Moving money between the person's own accounts is Transfers.
- Confidence is 0 to 1. Use 0.9 or more only when you clearly recognize the merchant or its purpose. If you are guessing,
  say 0.5 or less. Never invent a merchant you do not recognize: choose the most plausible category and lower the confidence.
- Learn from the examples the person already labeled: follow their conventions.
Reply with JSON only: {{"results": [{{"id": <number>, "category": "<name from the list>", "confidence": <0-1>}}]}}
with exactly one entry for every id you were given."""


def system_prompt() -> str:
    return SYSTEM.format(taxonomy="\n".join(f"- {k}: {v}" for k, v in TAXONOMY.items()))


def canonical(name: str) -> str | None:
    return _BY_LOWER.get(str(name).strip().lower())


# ---- candidates ---------------------------------------------------------------------------------

def candidates(txns: list[Transaction], existing: dict[str, dict], acct_types: dict[str, str] | None = None,
               redo_ai: bool = False) -> list[dict]:
    """Unique merchants that still need a label, biggest spend first."""
    acct_types = acct_types or {}
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        key = normalize(t.description)
        if key:
            groups[key].append(t)
    out = []
    for key, ts in groups.items():
        lab = existing.get(key)
        if lab and (lab["source"] == "user" or not redo_ai):
            continue
        if all(t.bank_category == MARK for t in ts):
            continue  # matched transfers between your own accounts are already settled
        amounts = [float(t.amount) for t in ts]
        out.append({
            "key": key,
            "example": Counter(t.description for t in ts).most_common(1)[0][0][:70],
            "n": len(ts), "total": round(sum(abs(a) for a in amounts), 2),
            "avg": round(statistics.mean(amounts), 2),
            "direction": "money in" if sum(a > 0 for a in amounts) > len(amounts) / 2 else "money out",
            "account": Counter(acct_types.get(t.account, "unknown") for t in ts).most_common(1)[0][0],
            "bank_label": next((b for b, _ in Counter(t.bank_category for t in ts if t.bank_category and t.bank_category != MARK).most_common(1)), ""),
        })
    return sorted(out, key=lambda c: -c["total"])


# ---- prompts ------------------------------------------------------------------------------------

def build_messages(batch: list[dict], examples: list[dict]) -> tuple[str, list[dict]]:
    system = system_prompt()
    shots = ""
    if examples:
        shots = "Already labeled by the person (follow their conventions):\n" + json.dumps(examples, indent=0) + "\n\n"
    items = [{"id": i, "merchant": c["example"], "times": c["n"], "typical_amount": c["avg"], "direction": c["direction"],
              "account": c["account"], "bank_label": c["bank_label"]} for i, c in enumerate(batch)]
    user = shots + "Categorize these merchants:\n" + json.dumps(items, indent=0)
    return system, [{"role": "user", "content": user}]


def parse_results(text: str, batch: list[dict]) -> list[dict]:
    """Tolerant JSON parsing. Anything missing or invalid becomes Other with low confidence, never silently dropped."""
    data = None
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        try:
            data = json.loads(candidate)
            break
        except (ValueError, TypeError):
            continue
    rows = data.get("results") if isinstance(data, dict) else data if isinstance(data, list) else []
    got: dict[int, tuple[str, float]] = {}
    for r in rows or []:
        try:
            i = int(r["id"])
            cat = canonical(r["category"])
            conf = max(0.0, min(1.0, float(r.get("confidence", 0.5))))
        except (KeyError, ValueError, TypeError):
            continue
        got[i] = (cat, conf) if cat else ("Other", min(conf, 0.3))
    return [{**c, "category": got.get(i, ("Other", 0.0))[0], "confidence": got.get(i, ("Other", 0.0))[1],
             "parsed": i in got} for i, c in enumerate(batch)]


# ---- engines ------------------------------------------------------------------------------------

def ollama_json(system: str, messages: list[dict], model: str) -> str:
    body = json.dumps({"model": model, "stream": False, "format": "json", "options": {"temperature": 0},
                       "messages": [{"role": "system", "content": system}] + messages}).encode()
    req = urllib.request.Request(advisor.ollama_url() + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["message"]["content"]


def claude_json(system: str, messages: list[dict], model: str) -> str:
    payload = {"model": model, "max_tokens": 8000, "system": system, "messages": messages}
    return "".join(advisor.claude_stream(payload))


def claude_payloads(cands: list[dict], examples: list[dict], model: str | None = None, size: int = 40) -> list[dict]:
    """The exact requests that would go to Anthropic. Merchant text only; no account numbers, no raw transactions."""
    out = []
    for i in range(0, len(cands), size):
        system, messages = build_messages(cands[i:i + size], examples)
        out.append({"model": model or advisor.CLAUDE_MODEL, "max_tokens": 8000, "system": system, "messages": messages})
    return out


def payloads_digest(payloads: list[dict]) -> str:
    return advisor.payload_digest({"batches": payloads})


def render_payloads(payloads: list[dict]) -> str:
    return "\n\n=== NEXT REQUEST ===\n\n".join(advisor.render_payload(p) for p in payloads)


# ---- run ----------------------------------------------------------------------------------------

def run(conn, cands: list[dict], engine: str = "local", model: str | None = None, examples: list[dict] | None = None,
        progress: Callable[[int, int], None] | None = None, size: int | None = None,
        complete: Callable[[str, list[dict], str], str] | None = None) -> dict:
    """Classify and save labels. Returns counts. `complete` is injectable for tests."""
    examples = examples if examples is not None else labels.user_examples(conn)
    model = model or (advisor.CLAUDE_MODEL if engine == "claude" else advisor.DEFAULT_MODEL)
    size = size or (40 if engine == "claude" else 15)
    complete = complete or (claude_json if engine == "claude" else ollama_json)
    saved = auto = review = unparsed = 0
    for i in range(0, len(cands), size):
        batch = cands[i:i + size]
        system, messages = build_messages(batch, examples)
        results = parse_results(complete(system, messages, model), batch)
        for r in results:
            if not r["parsed"]:
                unparsed += 1
                continue
            if labels.set_label(conn, r["key"], r["category"], "ai", r["confidence"], model, r["example"]):
                saved += 1
                auto += r["confidence"] >= labels.AI_MIN
                review += r["confidence"] < labels.AI_MIN
        if progress:
            progress(min(i + size, len(cands)), len(cands))
    return {"merchants": len(cands), "saved": saved, "applied_automatically": auto, "needs_review": review, "unparsed": unparsed}


# ---- accuracy -----------------------------------------------------------------------------------

def evaluate(conn, txns: list[Transaction], rules: dict, acct_types: dict[str, str], engine: str = "local",
             model: str | None = None, limit: int = 60, complete=None, preview_only: bool = False) -> dict:
    """Score the AI (and the old rules-only chain) against merchants YOU labeled. Each is held out of its own examples."""
    truth = {k: v["category"] for k, v in labels.load_all(conn).items() if v["source"] == "user"}
    cands = [c for c in candidates(txns, {}, acct_types) if c["key"] in truth][:limit]
    if len(cands) < 10:
        return {"error": f"Only {len(cands)} merchants have your labels. Label at least 10 (the review queue makes this quick) to measure accuracy."}
    held = {c["key"] for c in cands}
    shots = [e for e in labels.user_examples(conn, 200) if normalize(e["merchant"]) not in held][:25]
    model = model or (advisor.CLAUDE_MODEL if engine == "claude" else advisor.DEFAULT_MODEL)
    size = 40 if engine == "claude" else 15
    if preview_only:  # exactly what an evaluation would send, without sending it
        return {"payloads": claude_payloads(cands, shots, model, size)}
    complete = complete or (claude_json if engine == "claude" else ollama_json)
    base_rules = {**rules, LABELS_KEY: {}}
    first = {}
    for t in txns:
        first.setdefault(normalize(t.description), t)
    rows, ai_hits, base_hits = [], 0, 0
    for i in range(0, len(cands), size):
        batch = cands[i:i + size]
        system, messages = build_messages(batch, shots)
        for r in parse_results(complete(system, messages, model), batch):
            t = first[r["key"]]
            base = categorize(t.description, t.amount, base_rules, t.bank_category)
            exp = truth[r["key"]]
            ai_hits += r["category"] == exp
            base_hits += base == exp
            rows.append({"merchant": r["example"], "expected": exp, "ai": r["category"], "confidence": r["confidence"], "rules_only": base})
    n = len(rows)
    confident = [r for r in rows if r["confidence"] >= labels.AI_MIN]
    return {"n": n, "engine": engine, "model": model,
            "ai_accuracy": round(ai_hits / n * 100, 1), "rules_only_accuracy": round(base_hits / n * 100, 1),
            "confident_n": len(confident),
            "confident_accuracy": round(sum(r["ai"] == r["expected"] for r in confident) / len(confident) * 100, 1) if confident else None,
            "mistakes": [r for r in rows if r["ai"] != r["expected"]][:25]}
