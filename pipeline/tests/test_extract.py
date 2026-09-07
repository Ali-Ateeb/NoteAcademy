"""The deterministic half of extraction: cross-checking model output.

cross_check is what stops a hallucinated question reaching the database, so it
is tested against the shapes that actually go wrong.
"""

from pathlib import Path

from noteacademy_pipeline.extract import cross_check
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
