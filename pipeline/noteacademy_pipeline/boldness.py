"""Bold-vs-regular text detection that survives font-name obfuscation.

CAIE's newest structured papers (Oct/Nov 2024 on) embed every font under one
meaningless shared name ("AllAndNone") with identical flags on every span, so
neither the font name nor PyMuPDF's own bold flag carries any signal — a
question's own bold number and the regular prose next to it report exactly
the same font.

The raw glyph id PyMuPDF exposes through `Page.get_texttrace()`, underneath
the Unicode value it resolves that glyph to, still does: the same character
reliably uses a different embedded glyph outline depending on its actual
weight, even though the font's own name gives no hint. A short frequency
count — for a given character at a given size, which glyph id appears far
more often than any other — recovers "regular" as the majority glyph and
"bold" as anything else, since a real exam page has orders of magnitude more
regular text than bold labels.

That reasoning only holds for a letter or digit, though: punctuation like
"(" is shared between a part label ("(a)") and ordinary prose ("copper(II)
sulfate"), and a data-response paper can easily print *more* parts than it
does parenthesised chemical notation — inverting which glyph is actually the
majority one. Only a label's own letter or digit is ever used as the anchor
character for this reason; a leading "(" is skipped in favour of the "a"
right after it.

Frequency alone also thins out for a question number past about 5 or 6: a
question's own root digit appears exactly once as itself, and how often that
same digit turns up elsewhere depends entirely on what the paper happens to
calculate with, so "7" can easily have only two or three examples of each
weight to count — too few to trust a majority vote, and occasionally an
outright tie. Rendering the actual pixels settles it in that case instead:
a bold glyph reliably covers more of its own bounding box in ink than its
regular counterpart, which a frequency count cannot see but a pixel count
can, and unlike frequency this does not get less reliable the rarer the
character is — only slower, which is why it is used to break a tie rather
than to classify every character from the start.

Built once per document — the mapping is particular to that PDF's own
subsetted font, not portable to another one — and used only as a fallback: a
document whose font names already distinguish bold from regular is left
exactly as before, both because no fallback is needed there and because the
frequency count is calibrated for *this* ambiguity specifically; a stylistic
difference unrelated to weight (italics, a superscript) could just as easily
show up as "not the majority glyph" and would otherwise be misread as bold.
"""

from __future__ import annotations

from collections import Counter

import pymupdf
from PIL import Image

from .segment import BODY_BOTTOM, BODY_TOP

# Below this margin between the two most common glyphs for one (character,
# size), or below this combined sample size, frequency alone is not trusted
# and the pixel-density tiebreak below settles it instead.
_CONFIDENT_RATIO = 2.0
_CONFIDENT_SAMPLE = 20

# Ink darker than this (0 black, 255 white) counts as part of a glyph's own
# stroke rather than anti-aliasing haze at its edge.
_INK_THRESHOLD = 140
_RENDER_DPI = 600


def _is_anchor_candidate(codepoint: int) -> bool:
    """Excludes the watermark/tracking layer some of these same PDFs also
    embed (garbled, non-ASCII runs in a second obfuscated font, printed as
    ordinary text but never meant to be read), along with every punctuation
    mark — the latter deliberately, since a label's own letter or digit is
    the only reliable anchor (see the module docstring)."""
    char = chr(codepoint)
    return char.isascii() and char.isalnum()


class BoldChecker:
    """Answers "is this span bold" for one already-opened document.

    `majority_glyph` is `None` when the document's own font names already
    distinguish bold from regular — the common case — in which case this
    checker does nothing beyond the font-name check every caller already
    made before this project existed.
    """

    def __init__(
        self,
        majority_glyph: dict[tuple[str, float], int] | None,
        chars_by_page: dict[int, list[tuple[float, float, int, int]]],
    ) -> None:
        self._majority_glyph = majority_glyph
        # page index -> [(x0, y0, codepoint, glyph_id), ...], restricted to
        # the same alphanumeric-only candidates the majority-glyph table
        # itself was built from.
        self._chars_by_page = chars_by_page

    def is_bold(self, page_index: int, span: dict) -> bool:
        if "Bold" in span["font"]:
            return True
        if self._majority_glyph is None:
            return False

        anchor = self._anchor_char(page_index, span["bbox"])
        if anchor is None:
            return False
        codepoint, glyph_id = anchor

        majority = self._majority_glyph.get((chr(codepoint), round(span["size"], 1)))
        if majority is None:
            # This exact (character, size) combination never turned up
            # anywhere else in the document to compare against — nothing
            # says it is bold, so it is read the same as it always was.
            return False
        return glyph_id != majority

    def _anchor_char(
        self, page_index: int, bbox: tuple[float, float, float, float]
    ) -> tuple[int, int] | None:
        """The first letter or digit inside `bbox` — "(a)"'s own "a", not
        its opening paren — reading left to right. `chars_by_page` only
        ever holds alphanumeric candidates in practice (`build_bold_checker`
        filters at the source), but the check is repeated here too rather
        than trusted blindly, so this stays correct for any caller that
        builds one directly."""
        x0, y0, x1, y1 = bbox
        candidates = [
            (cx0, codepoint, glyph_id)
            for cx0, cy0, codepoint, glyph_id in self._chars_by_page.get(page_index, ())
            if y0 - 1.0 <= cy0 <= y1 + 1.0
            and x0 - 0.5 <= cx0 <= x1 + 0.5
            and _is_anchor_candidate(codepoint)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        _, codepoint, glyph_id = candidates[0]
        return codepoint, glyph_id


def _font_names_distinguish_bold(doc: pymupdf.Document) -> bool:
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if "Bold" in span["font"]:
                        return True
    return False


def _ink_density(page: pymupdf.Page, bbox: tuple[float, float, float, float]) -> float:
    """Fraction of a tightly-cropped render of one glyph that is genuinely
    dark — a bold glyph's own thicker stroke covers noticeably more of its
    bounding box than a regular one does at the same size."""
    pad = 0.3
    rect = pymupdf.Rect(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    matrix = pymupdf.Matrix(_RENDER_DPI / 72, _RENDER_DPI / 72)
    pixmap = page.get_pixmap(matrix=matrix, clip=rect, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
    pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
    total = len(pixels)
    if not total:
        return 0.0
    dark = sum(1 for value in pixels if value < _INK_THRESHOLD)
    return dark / total


def _average_ink_density(
    doc: pymupdf.Document, samples: list[tuple[int, tuple[float, float, float, float]]]
) -> float:
    densities = [_ink_density(doc[page_index], bbox) for page_index, bbox in samples]
    return sum(densities) / len(densities) if densities else 0.0


def build_bold_checker(doc: pymupdf.Document) -> BoldChecker:
    """One checker for the whole document, built once and reused per page.

    Skips the (comparatively expensive) glyph-frequency pass entirely when
    the document's own font names already work, which is true of every
    paper this pipeline handled before this module existed.
    """
    if _font_names_distinguish_bold(doc):
        return BoldChecker(majority_glyph=None, chars_by_page={})

    counts: dict[tuple[str, float], Counter[int]] = {}
    samples: dict[tuple[str, float, int], list[tuple[int, tuple[float, float, float, float]]]] = {}
    chars_by_page: dict[int, list[tuple[float, float, int, int]]] = {}

    for page in doc:
        page_chars: list[tuple[float, float, int, int]] = []
        for span in page.get_texttrace():
            size = round(span["size"], 1)
            for codepoint, glyph_id, _origin, bbox in span["chars"]:
                if not _is_anchor_candidate(codepoint):
                    continue
                # The running page number in the header is bold too, and for
                # a digit otherwise rare enough in the body it can easily
                # outweigh every genuinely regular use put together — this
                # is calibration, not the anchor lookup below, so it is the
                # one place page furniture actually needs to be kept out
                # rather than merely never queried.
                if BODY_TOP < bbox[1] < BODY_BOTTOM:
                    char = chr(codepoint)
                    counts.setdefault((char, size), Counter())[glyph_id] += 1
                    samples.setdefault((char, size, glyph_id), []).append((page.number, bbox))
                page_chars.append((bbox[0], bbox[1], codepoint, glyph_id))
        chars_by_page[page.number] = page_chars

    majority_glyph: dict[tuple[str, float], int] = {}
    for key, counter in counts.items():
        ranked = counter.most_common(2)
        if len(ranked) == 1:
            majority_glyph[key] = ranked[0][0]
            continue

        (glyph_a, count_a), (glyph_b, count_b) = ranked
        if count_a >= _CONFIDENT_RATIO * count_b or count_a + count_b >= _CONFIDENT_SAMPLE:
            majority_glyph[key] = glyph_a
            continue

        char, size = key
        density_a = _average_ink_density(doc, samples[(char, size, glyph_a)])
        density_b = _average_ink_density(doc, samples[(char, size, glyph_b)])
        # The regular (majority) glyph is the lighter of the two; a real
        # bold stroke covers more of its own box in ink, not less.
        majority_glyph[key] = glyph_a if density_a <= density_b else glyph_b

    return BoldChecker(majority_glyph=majority_glyph, chars_by_page=chars_by_page)
