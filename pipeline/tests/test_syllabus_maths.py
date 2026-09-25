"""The mathematics syllabus template: a two-column 'Notes and examples' table.

Built as a real PDF to the measured geometry (numbers at x=62, titles at x=96,
guidance column at x=309), because what goes wrong here is geometric: the right
column leaking into objectives, a wrapped line read out of order after a
superscript, a topic that runs across a page break.
"""

from __future__ import annotations

import pymupdf
import pytest

from noteacademy_pipeline.syllabus import parse_syllabus
from noteacademy_pipeline.syllabus_maths import is_mathematics_layout

NUM_X, TITLE_X, LEFT_X, INDENT_X = 62.4, 96.4, 62.4, 72.9
BULLET_X, BULLET_TEXT_X, NOTES_X = 79.4, 96.4, 309.0


def write(page, x, y, text, size=10, bold=False):
    page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv")


def topic_header(page, y, code, title):
    write(page, NUM_X, y, code, bold=True)
    write(page, TITLE_X, y, title, bold=True)
    write(page, NOTES_X, y, "Notes and examples", bold=True)


def section_header(page, y, number, title):
    write(page, NUM_X, y, number, size=13, bold=True)
    write(page, TITLE_X, y, title, size=13, bold=True)


@pytest.fixture
def maths_pdf(tmp_path):
    doc = pymupdf.open()

    cover = doc.new_page()
    write(cover, 60, 100, "Cambridge O Level", size=20)
    write(cover, 60, 140, "Mathematics (Syllabus D) 4024", size=20)
    write(cover, 60, 180, "Use this syllabus for exams in 2025, 2026 and 2027.")

    page = doc.new_page()
    write(page, 56.7, 60, "3  Subject content", size=18, bold=True)
    section_header(page, 100, "1", "Number")

    topic_header(page, 130, "1.1", "Types of number")
    write(page, LEFT_X, 155, "Identify and use:")
    for row, item in enumerate(["natural numbers", "integers", "prime numbers"]):
        y = 175 + row * 16
        write(page, BULLET_X, y, "*")           # the bullet glyph, on its own line
        write(page, BULLET_TEXT_X, y, item)
    write(page, NOTES_X, 155, "Example tasks include convert between forms")

    topic_header(page, 260, "1.2", "Standard form")
    write(page, NUM_X, 285, "1  Use the standard form A x 10n where n is a")
    # The continuation of a line that carries a superscript sits *above* it.
    write(page, INDENT_X, 277, "positive or negative integer.")
    write(page, NUM_X, 320, "2  Convert numbers into and out of standard form.")
    write(page, NOTES_X, 285, "Includes 6 x 10 to the power minus two")

    # A topic that runs over the page break.
    topic_header(page, 420, "1.3", "Ratio")
    write(page, NUM_X, 445, "1  Understand ratio in its simplest form.")

    page2 = doc.new_page()
    write(page2, 56.7, 60, "3  Subject content", size=18, bold=True)
    section_header(page2, 100, "1", "Number (continued)")
    write(page2, NUM_X, 130, "2  Divide a quantity in a given ratio.")
    write(page2, NOTES_X, 130, "guidance that must not become an objective")
    topic_header(page2, 200, "1.4", "Percentages")
    write(page2, NUM_X, 225, "1  Calculate a percentage of a quantity.")
    topic_header(page2, 300, "1.5", "Rates")
    write(page2, NUM_X, 325, "1  Use common measures of rate.")

    page3 = doc.new_page()
    write(page3, 56.7, 60, "4  Details of the assessment", size=18, bold=True)
    write(page3, NUM_X, 130, "1.9  Paper 1 is not a topic")

    path = tmp_path / "4024_syllabus.pdf"
    doc.save(path)
    doc.close()
    return path


def test_the_layout_is_recognised(maths_pdf, tmp_path):
    assert is_mathematics_layout(maths_pdf)

    plain = pymupdf.open()
    plain.new_page().insert_text((60, 100), "1.1  Motion", fontsize=10)
    path = tmp_path / "plain.pdf"
    plain.save(path)
    plain.close()
    assert not is_mathematics_layout(path)


def test_reads_sections_topics_and_the_years(maths_pdf):
    assert is_mathematics_layout(maths_pdf)
    syllabus = parse_syllabus(maths_pdf)
    assert syllabus.syllabus_code == "4024"
    assert syllabus.label == "2025-2027"
    assert [t.code for t in syllabus.walk()] == ["1", "1.1", "1.2", "1.3", "1.4", "1.5"]
    assert syllabus.sections[0].title == "Number"          # '(continued)' is not part of it
    assert syllabus.problems == []


def test_bullets_fold_into_their_statement(maths_pdf):
    topics = {t.code: t for t in parse_syllabus(maths_pdf).walk()}
    assert topics["1.1"].learning_objectives == [
        "Identify and use: natural numbers; integers; prime numbers"
    ]


def test_a_wrapped_line_after_a_superscript_is_read_in_order(maths_pdf):
    topics = {t.code: t for t in parse_syllabus(maths_pdf).walk()}
    assert topics["1.2"].learning_objectives[0] == (
        "Use the standard form A x 10n where n is a positive or negative integer."
    )
    assert topics["1.2"].learning_objectives[1] == "Convert numbers into and out of standard form."


def test_the_notes_column_is_never_an_objective(maths_pdf):
    text = " ".join(o for t in parse_syllabus(maths_pdf).walk() for o in t.learning_objectives)
    assert "guidance that must not" not in text
    assert "Includes 6 x 10" not in text
    assert "Example tasks" not in text


def test_a_topic_that_runs_over_a_page_break_keeps_both_halves(maths_pdf):
    topics = {t.code: t for t in parse_syllabus(maths_pdf).walk()}
    assert topics["1.3"].learning_objectives == [
        "Understand ratio in its simplest form.",
        "Divide a quantity in a given ratio.",
    ]


def test_content_stops_at_the_next_chapter(maths_pdf):
    assert "1.9" not in [t.code for t in parse_syllabus(maths_pdf).walk()]
