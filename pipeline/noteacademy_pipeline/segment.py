"""Deterministic segmentation of multiple-choice papers.

Validated against real CAIE papers (5054/11 May/June 2026, 40/40 questions
located with no false positives).

CAIE lays multiple-choice papers out on a strict grid: the question number sits
alone in a left gutter, and every other element of the question — stem,
diagram, options — is indented past it. That makes question boundaries a
geometric fact rather than something a model has to infer, and geometry does not
hallucinate.

So for MCQ papers the expensive part of the pipeline is not needed:

    answer key   -> parsed from the mark scheme's text layer   (markscheme.py)
    boundaries   -> found from span geometry                   (this module)
    display      -> cropped from the page                      (render.py)

leaving vision extraction as an optional pass for searchable *text*, and as a
cross-check. Structured papers still need the full pipeline — they have no such
regular grid — but Paper 1 is exactly the wedge that needs to be cheap.

The constants below describe CAIE's A4 multiple-choice template. They are
checked, not assumed: `segment_mcq_paper` refuses a paper whose geometry does not
match rather than emitting silently wrong crops.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

# A4 in points, the format every CAIE paper examined uses.
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

# The question-number gutter. Stems and options begin around x=72; numbers sit
# at x≈49.6. 60 separates them with room to spare.
GUTTER_MAX_X = 60.0

# Body of the page: below the running page number, above the footer rule.
BODY_TOP = 45.0
BODY_BOTTOM = 780.0

# Horizontal extent of a cropped question.
CROP_LEFT = 45.0
CROP_RIGHT = 555.0

# Gap left above the following question so a crop does not clip its neighbour.
CROP_GAP = 8.0

# CAIE's running footer: the copyright acknowledgement, the UCLES line and the
# paper code, set at 7-8pt in the bottom of the last page. Question text is
# 11pt (9.8-10.4pt on the subjects with a data page), so size alone separates
# them — but size alone would also catch a graph's axis labels, which are small
# and *are* part of the question. Both tests together do not: the bottom 90pt
# of the page is the template's footer strip, and no question's artwork lives
# there.
#
# Without this the last question on the last page swallows the whole notice:
# its crop shows a student a paragraph of legal boilerplate, and its text index
# is the boilerplate rather than the question.
FOOTER_MAX_SIZE = 9.0
FOOTER_TOP = 690.0

# The rule drawn above the copyright notice: full width, hairline. Narrow
# enough a test that a question's own artwork cannot match it — a diagram that
# happened to reach the footer strip would still have to be a 400pt-wide line
# under 2pt tall to be mistaken for the separator.
FOOTER_RULE_MIN_WIDTH = 400.0
FOOTER_RULE_MAX_HEIGHT = 2.0


@dataclass
class QuestionRegion:
    number: int
    page_number: int          # 1-indexed
    bbox: tuple[float, float, float, float]

    @property
    def display_label(self) -> str:
        return str(self.number)


def find_question_starts(page: pymupdf.Page) -> list[tuple[int, float]]:
    """Question numbers on one page, as (number, y-position of its top).

    A question number is a span that is entirely digits and sits in the left
    gutter, within the body of the page. The gutter test is what excludes the
    running page number, the footer, and every numeral inside a graph's axis
    labels — those are all indented or outside the body.
    """
    starts: list[tuple[int, float]] = []

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                x0, y0 = span["bbox"][0], span["bbox"][1]
                if text.isdigit() and x0 < GUTTER_MAX_X and BODY_TOP < y0 < BODY_BOTTOM:
                    starts.append((int(text), y0))

    starts.sort(key=lambda item: item[1])
    return starts


def is_footer(span: dict) -> bool:
    """Is this span part of the page's running footer rather than a question?"""
    return span["size"] < FOOTER_MAX_SIZE and span["bbox"][1] > FOOTER_TOP


def is_footer_rule(rect: pymupdf.Rect) -> bool:
    """Is this the hairline above the copyright notice rather than artwork?"""
    return (
        rect.y0 > FOOTER_TOP
        and rect.height < FOOTER_RULE_MAX_HEIGHT
        and rect.width > FOOTER_RULE_MIN_WIDTH
    )


def content_bottom(page: pymupdf.Page, top: float, limit: float) -> float | None:
    """Lowest point of any text or artwork between `top` and `limit`.

    Used to trim trailing whitespace from the last crop on a page. Returns None
    when the region is empty, in which case the caller keeps its own bound.
    """
    lowest: float | None = None

    def consider(y1: float) -> None:
        nonlocal lowest
        if top < y1 <= limit and (lowest is None or y1 > lowest):
            lowest = y1

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if is_footer(span):
                    continue
                consider(span["bbox"][3])

    for drawing in page.get_drawings():
        if is_footer_rule(drawing["rect"]):
            continue
        consider(drawing["rect"].y1)

    return lowest


def segment_mcq_paper(pdf_path: Path) -> tuple[list[QuestionRegion], list[str]]:
    """Locate every question in a multiple-choice paper.

    Returns the regions and a list of problems. A non-empty problem list means
    the paper does not match the template this module understands — send it down
    the vision path rather than trusting these boxes.
    """
    regions: list[QuestionRegion] = []
    problems: list[str] = []

    with pymupdf.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            page_number = page_index + 1

            if (
                abs(page.rect.width - PAGE_WIDTH) > 2
                or abs(page.rect.height - PAGE_HEIGHT) > 2
            ):
                problems.append(
                    f"page {page_number} is {page.rect.width:.0f}x{page.rect.height:.0f}pt, "
                    f"not the expected A4 template"
                )
                continue

            starts = find_question_starts(page)
            for i, (number, y0) in enumerate(starts):
                # A question runs to the next one on the page, or to the footer.
                next_y = starts[i + 1][1] if i + 1 < len(starts) else BODY_BOTTOM
                bottom = max(y0 + 1.0, next_y - CROP_GAP)

                # The last question on a page would otherwise be padded out to
                # the footer with blank paper. Trim to where the ink actually
                # stops — a crop with an inch of whitespace under it looks
                # broken on a question card.
                if i + 1 == len(starts):
                    ink = content_bottom(page, y0, bottom)
                    if ink is not None:
                        bottom = min(bottom, ink + CROP_GAP)
                regions.append(
                    QuestionRegion(
                        number=number,
                        page_number=page_number,
                        bbox=(CROP_LEFT, y0 - CROP_GAP, CROP_RIGHT, bottom),
                    )
                )

    problems.extend(validate_regions(regions))
    return regions, problems


def validate_regions(regions: list[QuestionRegion]) -> list[str]:
    """Check the segmentation produced a complete, ordered, sane set.

    A paper that yields 38 of 40 questions, or the same number twice, is a
    segmentation failure — not a short paper. Saying so is what stops a bank
    quietly acquiring holes.
    """
    problems: list[str] = []
    numbers = [r.number for r in regions]

    if not numbers:
        return ["no questions found"]

    if numbers != sorted(numbers):
        problems.append("question numbers are not in ascending order across the paper")

    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        problems.append(f"duplicate question numbers: {duplicates}")

    expected = set(range(1, max(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        problems.append(f"missing question numbers: {missing}")

    for region in regions:
        x0, y0, x1, y1 = region.bbox
        if y1 <= y0 or x1 <= x0:
            problems.append(f"degenerate crop box on question {region.number}")

    return problems
