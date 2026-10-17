"""Tiny markdown-to-reportlab converter for model-written narrative (headings, bullets, bold, numbered items)."""
import re
from xml.sax.saxutils import escape

from reportlab.platypus import Paragraph, Spacer

BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")


def inline(text: str) -> str:
    t = escape(text.strip())
    t = BOLD.sub(r"<b>\1</b>", t)
    return ITALIC.sub(r"<i>\1</i>", t)


def to_flowables(md: str, styles: dict) -> list:
    """styles: h1, h2, h3, body, bullet. Unknown markup falls back to plain paragraphs, never raises."""
    out = []
    for raw in md.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            out.append(Spacer(1, 3))
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            out.append(Paragraph(inline(m.group(2)), styles["h2" if len(m.group(1)) <= 2 else "h3"]))
            continue
        m = re.match(r"^\s*[-*•]\s+(.*)$", line)
        if m:
            out.append(Paragraph("&bull; " + inline(m.group(1)), styles["bullet"]))
            continue
        m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
        if m:
            out.append(Paragraph(f"{m.group(1)}. " + inline(m.group(2)), styles["bullet"]))
            continue
        if line.strip() in ("---", "***"):
            out.append(Spacer(1, 6))
            continue
        out.append(Paragraph(inline(line), styles["body"]))
    return out
