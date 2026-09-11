"""Bold-vs-regular detection that survives font-name obfuscation.

`BoldChecker`'s decision logic is tested directly, against hand-built
frequency tables — synthesising a PDF whose font name gives no signal at
all (the exact failure mode this module exists for) is not something
PyMuPDF's own text-insertion API can produce, short of hand-crafting a font
subset byte for byte. `build_bold_checker`'s own PDF-facing half (does this
document's font already say which spans are bold) is tested against a real,
ordinarily-built PDF instead, where it is expected to take the fast path.
"""

from pathlib import Path

import pymupdf

from noteacademy_pipeline.boldness import BoldChecker, build_bold_checker


def _span(text: str, x0: float, y0: float, x1: float, y1: float, size: float = 11.0) -> dict:
    return {"text": text, "font": "AllAndNone", "bbox": (x0, y0, x1, y1), "size": size}


class TestBoldCheckerFontNameShortcut:
    def test_a_bold_named_font_is_bold_regardless_of_calibration(self):
        checker = BoldChecker(majority_glyph=None, chars_by_page={})
        span = {"text": "8", "font": "Helvetica-Bold", "bbox": (0, 0, 5, 5), "size": 11.0}
        assert checker.is_bold(0, span) is True

    def test_no_calibration_and_no_bold_name_reads_as_regular(self):
        checker = BoldChecker(majority_glyph=None, chars_by_page={})
        span = _span("8", 0, 0, 5, 5)
        assert checker.is_bold(0, span) is False


class TestBoldCheckerGlyphFallback:
    def test_the_non_majority_glyph_is_bold(self):
        # "1" mostly renders as glyph 102 (regular); glyph 15 is the rarer,
        # bold variant — the exact shape of the real "AllAndNone" papers
        # this module was built for.
        majority_glyph = {("1", 11.0): 102}
        chars_by_page = {0: [(49.6, 63.8, ord("1"), 15)]}
        checker = BoldChecker(majority_glyph, chars_by_page)
        span = _span("1", 49.6, 63.8, 55.8, 74.8)
        assert checker.is_bold(0, span) is True

    def test_the_majority_glyph_is_regular(self):
        majority_glyph = {("1", 11.0): 102}
        chars_by_page = {0: [(49.6, 63.8, ord("1"), 102)]}
        checker = BoldChecker(majority_glyph, chars_by_page)
        span = _span("1", 49.6, 63.8, 55.8, 74.8)
        assert checker.is_bold(0, span) is False

    def test_an_uncalibrated_character_reads_as_regular(self):
        # This exact (character, size) never turned up anywhere else in the
        # document to compare against — nothing says it is bold.
        checker = BoldChecker(majority_glyph={}, chars_by_page={0: [(0, 0, ord("9"), 5)]})
        span = _span("9", 0, 0, 6, 11)
        assert checker.is_bold(0, span) is False

    def test_a_leading_paren_is_skipped_in_favour_of_the_letter_after_it(self):
        # "(" is shared between a part label and ordinary parenthesised
        # prose ("copper(II) sulfate") and is not a reliable anchor on its
        # own — the inner letter is used instead.
        majority_glyph = {
            ("(", 11.0): 115,  # majority "(" happens to be the label's own
            ("a", 11.0): 14,  # majority "a" is the ordinary, regular one
        }
        chars_by_page = {
            0: [
                (72.3, 63.8, ord("("), 115),
                (75.9, 63.8, ord("a"), 35),  # the rarer, bold "a"
                (79.5, 63.8, ord(")"), 115),
            ]
        }
        checker = BoldChecker(majority_glyph, chars_by_page)
        span = _span("(a)", 72.3, 63.8, 83.0, 74.8)
        assert checker.is_bold(0, span) is True

    def test_no_character_falls_inside_the_span_reads_as_regular(self):
        checker = BoldChecker(majority_glyph={("1", 11.0): 102}, chars_by_page={0: []})
        span = _span("1", 49.6, 63.8, 55.8, 74.8)
        assert checker.is_bold(0, span) is False


class TestBuildBoldChecker:
    def test_a_normally_named_font_takes_the_fast_path(self, tmp_path: Path):
        # A document whose font names already distinguish bold from
        # regular needs no glyph-frequency calibration at all.
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((49.6, 63.8), "1", fontsize=11, fontname="Helvetica-Bold")
        page.insert_text((72.3, 63.8), "State the thing.", fontsize=11, fontname="Helvetica")
        out = tmp_path / "normal.pdf"
        doc.save(out)
        doc.close()

        with pymupdf.open(out) as reopened:
            checker = build_bold_checker(reopened)

        assert checker._majority_glyph is None
