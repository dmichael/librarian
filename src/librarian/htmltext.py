"""Shared HTML-to-text conversion.

One home for tag stripping so callers don't each reimplement the regexes. The
modes capture the distinct needs across the codebase; the core tag removal is
shared, so a future fix (e.g. entity unescaping) lands in one place.
"""
import re

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"<img[^>]*>")
_BR_RE = re.compile(r"<br\s*/?>")
_P_OPEN_RE = re.compile(r"<p[^>]*>")
_LI_OPEN_RE = re.compile(r"<li[^>]*>")


def html_to_text(html: str, mode: str = "flat") -> str:
    """Convert HTML to plain text.

    Modes:
      - "strip":      remove tags only (no whitespace normalization)
      - "flat":       remove tags, collapse all whitespace to single spaces
      - "lines":      remove tags, collapse spaces but preserve newlines
      - "structured": map <br>/<p>/<li> to newlines/bullets, drop <img>
    """
    if not html:
        return ""

    if mode == "structured":
        text = _IMG_RE.sub("", html)
        text = _BR_RE.sub("\n", text)
        text = _P_OPEN_RE.sub("", text)
        text = text.replace("</p>", "\n\n")
        text = _LI_OPEN_RE.sub("  - ", text)
        text = text.replace("</li>", "\n")
        text = _TAG_RE.sub("", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    text = _TAG_RE.sub("", html)
    if mode == "strip":
        return text.strip()
    if mode == "lines":
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n\s*\n", "\n", text)
        return text.strip()
    # "flat" (default)
    return re.sub(r"\s+", " ", text).strip()
