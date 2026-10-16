"""Local advisor chat over Ollama. Only talks to a loopback Ollama server."""
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
