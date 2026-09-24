"""Clean control characters out of text lifted from a paper's text layer.

Two different things arrive as control characters, and they need opposite
treatment:

* The barcode in every page's header strip is a *font*: it extracts as a page
  number, a comma, a run of \\x01-\\x08 and another comma. It is page
  furniture, and when a question's crop runs to the end of a page the strip
  gets glued onto the end of that question's text. It carries nothing, so it is
  removed (together with the page number in front of it, and a "BLANK PAGE"
  label if the strip belongs to one).

* Mark schemes set some symbols in a symbol font whose glyph extracts as
  \\x01: the reaction arrow between the two sides of an equation, and the tick
  in a tick-box table. Dropping it would leave "2H2O2   2H2O + O2", which
  teaches the wrong thing, so it is replaced with the symbol it draws.
"""

from __future__ import annotations

import re

_CTRL = r"\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
_CTRL_RE = re.compile(f"[{_CTRL}]")

_HEADER_STRIP_RE = re.compile(
    rf"(?:(?<=\s)\d{{1,2}}[ \t]+)?(?:BLANK PAGE[ \t]+)?,[{_CTRL}][{_CTRL}, ]*"
)

# The arrow always sits between two formulas, spaced on both sides: a species on
# the left (ends in a letter, digit, bracket, charge sign) and one on the right
# (starts with a capital, digit or bracket -- a coefficient or an element).
# A tick never does: it leads a line, or is jammed against an x.
_REACTION_ARROW_RE = re.compile(
    r"(?<=[A-Za-z0-9)\]+–−])[ \t]+\x01[ \t]+(?=[A-Z0-9(])"
)


def has_control_characters(text: str | None) -> bool:
    return text is not None and _CTRL_RE.search(text) is not None


def clean_extracted_text(text: str | None, *, symbols: bool = False) -> str | None:
    """`text` with control characters removed. Unchanged when it has none.

    `symbols=True` is for mark schemes: it turns the symbol-font \\x01 into the
    arrow or tick it draws instead of dropping it.
    """
    if text is None or not _CTRL_RE.search(text):
        return text

    # A NUL is what a font's empty glyph extracts as; Postgres refuses it too.
    cleaned = _HEADER_STRIP_RE.sub("", text.replace("\x00", ""))
    if symbols:
        cleaned = _REACTION_ARROW_RE.sub(" → ", cleaned)
        cleaned = cleaned.replace("\x01", "✓")
    cleaned = _CTRL_RE.sub(" ", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned.strip()
