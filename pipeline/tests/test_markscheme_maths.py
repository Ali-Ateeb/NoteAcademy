"""The mathematics mark scheme is a four-column table read by geometry.

Built as real PDFs to the measured layouts of two series, because what goes
wrong is where things sit: a Marks column mistaken for question numbers, a
preamble list mistaken for a table, a label split across tokens.
"""

from __future__ import annotations

import pymupdf
import pytest

from noteacademy_pipeline.markscheme_maths import (
    is_mathematics_mark_scheme,
    parse_mathematics_mark_scheme,
)


def write(page, x, y, text, bold=False):
    page.insert_text((x, y), text, fontsize=10, fontname="hebo" if bold else "helv")


@pytest.fixture
def modern(tmp_path):
    """2024 layout: combined labels, marks in a column at x=314, partial marks beside."""
    doc = pymupdf.open()
    preamble = doc.new_page()
    write(preamble, 50, 100, "Mathematics Specific Marking Principles")
    write(preamble, 50, 130, "1")           # numbered list, shaped like question numbers
    write(preamble, 70, 130, "Unless a method has been specified, any method is allowed")
    write(preamble, 50, 160, "2")
    write(preamble, 70, 160, "Answers may be given as fractions or decimals")

    page = doc.new_page()
    write(page, 60, 70, "Question", bold=True)
    write(page, 176, 70, "Answer", bold=True)
    write(page, 287, 70, "Marks", bold=True)
    write(page, 402, 70, "Partial Marks", bold=True)

    write(page, 70, 100, "1(a)")
    write(page, 115, 100, "25")
    write(page, 314, 100, "1")

    write(page, 70, 140, "1(b)(i)")
    write(page, 115, 140, "450 oe")
    write(page, 314, 140, "2 B1 for 3.5 used")

    # A lettered item inside sub-part (ii): two rows, folded into one leaf.
    write(page, 60, 200, "8(a)(ii)(a)")
    write(page, 115, 200, "2x+3 soi")
    write(page, 306, 200, "M1")
    write(page, 60, 230, "8(a)(ii)(b)")
    write(page, 115, 230, "y = 2.5 drawn")
    write(page, 306, 230, "M1")
    write(page, 306, 244, "A1")

    write(page, 70, 300, "9")
    write(page, 115, 300, "Enlargement")
    write(page, 314, 300, "3")

    path = tmp_path / "4024_ms.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def legacy(tmp_path):
    """2016 layout: '10' and '(a)' as separate tokens, '(c) (i)' spaced, '2*' marks."""
    doc = pymupdf.open()
    page = doc.new_page()
    write(page, 50, 80, "Question", bold=True)
    write(page, 189, 80, "Answers", bold=True)
    write(page, 318, 80, "Mark", bold=True)
    write(page, 421, 80, "Part marks", bold=True)

    write(page, 50, 110, "10")
    write(page, 72, 110, "(a)")
    write(page, 112, 110, "-3.5 or any equivalent")
    write(page, 326, 110, "1")

    write(page, 72, 140, "(b)")
    write(page, 112, 140, "3 + 10x")
    write(page, 326, 140, "2*")
    write(page, 356, 140, "M1 for 5 = 4 + 3x")

    write(page, 50, 180, "11 (c) (i)")
    write(page, 112, 180, "110")
    write(page, 326, 180, "2ft")

    write(page, 90, 210, "(ii)")
    write(page, 112, 210, "165")
    write(page, 326, 210, "1")

    path = tmp_path / "4024_ms_old.pdf"
    doc.save(path)
    doc.close()
    return path


def test_the_layout_is_recognised(modern, legacy, tmp_path):
    assert is_mathematics_mark_scheme(modern)
    assert is_mathematics_mark_scheme(legacy)

    prose = pymupdf.open()
    prose.new_page().insert_text((60, 100), "1  B1 for the answer", fontsize=10)
    path = tmp_path / "prose.pdf"
    prose.save(path)
    prose.close()
    assert not is_mathematics_mark_scheme(path)


def test_marks_come_from_the_marks_column_not_from_question_numbers(modern):
    entries = {e.display_label: e for e in parse_mathematics_mark_scheme(modern)}
    assert entries["1(a)"].marks == 1
    assert entries["1(b)(i)"].marks == 2
    assert entries["9"].marks == 3


def test_the_marking_principles_before_the_table_are_not_entries(modern):
    labels = [e.display_label for e in parse_mathematics_mark_scheme(modern)]
    assert "1" not in labels and "2" not in labels
    assert labels == ["1(a)", "1(b)(i)", "8(a)(ii)", "9"]


def test_lettered_items_inside_a_subpart_fold_into_it_and_their_marks_add(modern):
    entry = next(e for e in parse_mathematics_mark_scheme(modern) if e.display_label == "8(a)(ii)")
    assert entry.marks == 3                      # M1, then M1 and A1
    assert "2x+3 soi" in entry.content and "y = 2.5 drawn" in entry.content


def test_partial_marks_are_kept_with_the_answer(modern):
    entry = next(e for e in parse_mathematics_mark_scheme(modern) if e.display_label == "1(b)(i)")
    assert "450 oe" in entry.content and "B1 for 3.5 used" in entry.content


def test_split_and_spaced_labels_are_read_into_one_path(legacy):
    labels = [e.display_label for e in parse_mathematics_mark_scheme(legacy)]
    assert labels == ["10(a)", "10(b)", "11(c)(i)", "11(c)(ii)"]


def test_starred_and_follow_through_marks_are_numbers(legacy):
    entries = {e.display_label: e for e in parse_mathematics_mark_scheme(legacy)}
    assert entries["10(b)"].marks == 2           # "2*"
    assert entries["11(c)(i)"].marks == 2        # "2ft"
    assert "M1 for 5 = 4 + 3x" in entries["10(b)"].content
