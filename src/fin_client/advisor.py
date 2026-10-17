"""Local advisor chat over Ollama. Only talks to a loopback Ollama server."""
import copy
import hashlib
import json
import os
import urllib.request
from collections.abc import Callable, Iterator
from urllib.parse import urlparse

DEFAULT_MODEL = "llama3:8b"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

SYSTEM_PROMPT = """You are a careful personal-finance coach for one person, running fully offline.
You are NOT a licensed financial advisor; give educational guidance and say so briefly when stakes are high
(investing, taxes, large debt decisions).

Rules:
- Ground every claim in the JSON snapshot below. Quote its numbers; never invent balances, rates, ages, or income.
- If a needed fact is missing (debts and interest rates, account balances, age, retirement goals, tax filing status,
  employer match), say what is missing and ask the user for it instead of guessing. They can store it in profile.json.
- The "planning" section holds exact, code-computed results (debt payoff scenarios, emergency fund targets, retirement
  projections). Quote and explain those numbers; never recompute or contradict them. Follow its suggested_priority_order
  unless the user's situation clearly calls for something else, and say why. Mention "assumptions" that matter.
- For taxes, use planning.tax (federal only, ordinary income). Say what it does not model (state, FICA, credits) when
  relevant, and note that gross income comes from the user's profile, not from bank deposits (which are after-tax).
- Show simple arithmetic only for simple things (e.g. a percentage). Prefer concrete next steps with dollar amounts.
- Cover budgeting, emergency fund, debt payoff, investing and retirement, and taxes when relevant; mention tradeoffs.
- Be concise. Do not moralize about spending.

SNAPSHOT:
"""


def ollama_url() -> str:
    """Resolve the Ollama endpoint, refusing anything that is not loopback."""
    raw = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).hostname
    if host not in LOOPBACK:
        raise RuntimeError(
            f"Refusing non-local Ollama host {host!r}: financial data must not leave this machine."
        )
    return raw.rstrip("/")


Transport = Callable[[list[dict], str], Iterator[str]]


def ollama_transport(messages: list[dict], model: str) -> Iterator[str]:
    body = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(
        ollama_url() + "/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        for line in resp:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            yield chunk.get("message", {}).get("content", "")


class Advisor:
    def __init__(self, snapshot: dict, model: str = DEFAULT_MODEL, transport: Transport = ollama_transport):
        self.model, self.transport = model, transport
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT + json.dumps(snapshot, indent=1)}
        ]

    def ask(self, question: str) -> Iterator[str]:
        self.messages.append({"role": "user", "content": question})
        parts: list[str] = []
        for piece in self.transport(self.messages, self.model):
            parts.append(piece)
            yield piece
        self.messages.append({"role": "assistant", "content": "".join(parts)})


# --- Claude (cloud) engine: opt-in, previewed, digest-approved --------------------------------------

CLAUDE_MODEL = os.environ.get("FIN_CLIENT_CLAUDE_MODEL", "claude-opus-5-5")
MAX_REPLY_TOKENS = 4000


def redact_snapshot(snap: dict, include_merchants: bool = False) -> dict:
    """Copy of the snapshot that is safer to send off-machine.

    Debt/account names (which can carry card digits) become "Debt 1..n". Recurring merchant names become
    "Recurring item n" unless include_merchants. Amounts, rates and categories are kept: advice needs them.
    """
    snap = copy.deepcopy(snap)
    names: set[str] = {d["name"] for d in snap.get("user_profile", {}).get("debts", []) if "name" in d}
    for sc in snap.get("planning", {}).get("debt_payoff", {}).get("scenarios", []):
        names.update(sc.get("payoff_order") or [])
    mapping = {n: f"Debt {i + 1}" for i, n in enumerate(sorted(names))}
    if not include_merchants:
        for i, r in enumerate(snap.get("recurring_items", [])):
            mapping[r["merchant"]] = f"Recurring item {i + 1}"
    ordered = sorted(mapping.items(), key=lambda kv: -len(kv[0]))

    def walk(x):
        if isinstance(x, str):
            for old, new in ordered:
                x = x.replace(old, new)
            return x
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x

    return walk(snap)


def build_claude_payload(snapshot: dict, history: list[dict], question: str, include_merchants: bool = False,
                         model: str | None = None) -> dict:
    """The exact request body that would be sent to Anthropic, minus the API key."""
    safe = redact_snapshot(snapshot, include_merchants)
    turns = [{"role": m["role"], "content": str(m["content"])} for m in history
             if m.get("role") in ("user", "assistant")][-12:]
    return {"model": model or CLAUDE_MODEL, "max_tokens": MAX_REPLY_TOKENS,
            "system": SYSTEM_PROMPT + json.dumps(safe, indent=1),
            "messages": turns + [{"role": "user", "content": question}]}


def payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def render_payload(payload: dict) -> str:
    """Human-readable version of exactly what will be sent."""
    parts = [f"MODEL: {payload['model']}", "", "SYSTEM PROMPT + YOUR NUMBERS:", payload["system"], "", "CONVERSATION:"]
    parts += [f"[{m['role']}] {m['content']}" for m in payload["messages"]]
    return "\n".join(parts)


def claude_ready() -> bool:
    """True if an API credential is visible to this process. Never reads or returns the key itself."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def claude_stream(payload: dict) -> Iterator[str]:
    """Send the approved payload to Claude and yield text as it streams. Needs the `claude` extra."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The Claude option needs the SDK: pip install \"fin-client[claude]\"") from e
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=payload["model"], max_tokens=payload["max_tokens"], system=payload["system"],
        messages=payload["messages"], output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"},
    ) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        yield "\n[Claude declined to answer this one. Try rephrasing, or use the local advisor.]"
