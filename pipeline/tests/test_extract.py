"""The deterministic half of extraction: cross-checking model output, and the
pre-filter that skips the vision call altogether for a page confident enough
not to need it.

cross_check is what stops a hallucinated question reaching the database, so it
is tested against the shapes that actually go wrong. _looks_like_non_content
is tested the same way, from the other direction: every shape that must still
reach the model, and the narrow shape that may skip it.
"""

from pathlib import Path

from noteacademy_pipeline.extract import _looks_like_non_content, cross_check
from noteacademy_pipeline.render import RenderedPage
from noteacademy_pipeline.schemas import BoundingBox, ExtractedPage, ExtractedQuestion


def make_page(text: str) -> RenderedPage:
    return RenderedPage(
        page_number=3,
        png_path=Path("unused.png"),
        text=text,
        width_pt=595.0,
        height_pt=842.0,
    )


def make_question(label: str, bbox: tuple[float, float, float, float]) -> ExtractedQuestion:
    return ExtractedQuestion(
        display_label=label,
        question_type="structured",
        question_text="text",
        bbox=BoundingBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
    )


PAGE_TEXT = "4 A car accelerates uniformly. 5 State the unit of force. " * 3


def test_clean_page_reports_nothing():
    page = make_page(PAGE_TEXT)
    extracted = ExtractedPage(
        page_number=3,
        is_content_page=True,
        questions=[make_question("4", (50, 60, 500, 300))],
    )
    assert cross_check(page, extracted) == []


def test_flags_a_question_number_absent_from_the_page():
    page = make_page(PAGE_TEXT)
    extracted = ExtractedPage(
        page_number=3,
        is_content_page=True,
        questions=[make_question("19", (50, 60, 500, 300))],
    )
    problems = cross_check(page, extracted)
    assert any("19" in p for p in problems)


def test_flags_a_bbox_that_runs_off_the_page():
    page = make_page(PAGE_TEXT)
    extracted = ExtractedPage(
        page_number=3,
        is_content_page=True,
        questions=[make_question("4", (50, 60, 500, 2000))],
    )
    assert any("outside the page" in p for p in cross_check(page, extracted))


def test_flags_a_degenerate_bbox():
    page = make_page(PAGE_TEXT)
    extracted = ExtractedPage(
        page_number=3,
        is_content_page=True,
        questions=[make_question("4", (300, 60, 100, 30))],
    )
    assert any("degenerate" in p for p in cross_check(page, extracted))


def test_skips_the_text_check_on_a_scanned_page():
    # No text layer means no ground truth to check against. Reporting phantom
    # problems for every scanned page would train reviewers to ignore the queue.
    page = make_page("")
    extracted = ExtractedPage(
        page_number=3,
        is_content_page=True,
        questions=[make_question("19", (50, 60, 500, 300))],
    )
    assert cross_check(page, extracted) == []


def test_blank_page_is_skipped():
    page = make_page(
        "BLANK PAGE\n\nThis page has been left blank intentionally.\n"
        "© UCLES 2019 5054/11/O/N/19"
    )
    assert _looks_like_non_content(page)


def test_cover_page_is_skipped():
    page = make_page(
        "Cambridge International Examinations\n"
        "Cambridge International General Certificate of Secondary Education\n"
        "PHYSICS 5054/11\n"
        "Paper 1 Multiple Choice October/November 2019\n"
        "1 hour\n"
        "Candidate Name\n"
        "Additional Materials: Multiple Choice Answer Sheet\n"
        "READ THESE INSTRUCTIONS FIRST\n"
        "This document consists of 12 printed pages.\n"
    )
    assert _looks_like_non_content(page)


def test_formula_sheet_is_skipped():
    page = make_page(
        "DATA AND FORMULAE\n"
        "List of formulae\n"
        "speed = distance / time\n"
        "v = u + at\n"
        "acceleration = change in velocity / time taken\n"
    )
    assert _looks_like_non_content(page)


def test_a_real_page_missing_only_the_stock_phrase_is_not_skipped():
    # Short and has no mark bracket or numbered line, but nothing here is one
    # of CAIE's own stock phrases — the point of requiring both signals, not
    # just the absence of content markers.
    page = make_page("Some incidental text with nothing recognisable on it.")
    assert not _looks_like_non_content(page)


def test_real_question_content_is_never_skipped_even_with_a_stock_phrase():
    # A genuine content page can still legitimately mention "additional
    # materials" in passing (a question referencing the answer sheet, say) —
    # the mark bracket must always win.
    page = make_page(
        PAGE_TEXT + " Additional materials may be used for this calculation. [2]"
    )
    assert not _looks_like_non_content(page)


def test_mcq_page_with_option_letters_is_not_skipped():
    page = make_page(
        "4 Which quantity is a vector?\n"
        "A acceleration\n"
        "B density\n"
        "C energy\n"
        "D speed\n"
    )
    assert not _looks_like_non_content(page)


def test_scanned_page_is_never_skipped():
    # No text layer at all: there is nothing here to reason about, so this
    # must always fall through to vision regardless of stock phrases.
    page = make_page("blank page")
    assert not _looks_like_non_content(page)
