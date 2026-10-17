"""Turn messy bank descriptions into a stable merchant key, so one label covers every variant of a merchant."""
import re

STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
          "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
          "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"}
PROCESSORS = re.compile(r"^(?:SQ|TST|PP|PAYPAL|SP|CKO|PY|DD|GOOGLE|FS|BT|IC)\s*\*\s*", re.I)
NOISE = {"POS", "DEBIT", "PURCHASE", "CARD", "CHECKCARD", "RECURRING", "PAYMENT", "WWW", "COM", "INC", "LLC", "THE", "ONLINE", "PMT"}


def normalize(description: str, max_tokens: int = 3) -> str:
    s = PROCESSORS.sub("", description.upper().strip())
    s = re.sub(r"\d[\d\-/]*", " ", s)            # store numbers, dates, references
    s = re.sub(r"[^A-Z&' ]", " ", s)
    tokens = [t for t in s.split() if t not in NOISE and len(t) > 1 or t in ("&",)]
    while tokens and tokens[-1] in STATES:        # trailing state code
        tokens.pop()
    return " ".join(tokens[:max_tokens])
