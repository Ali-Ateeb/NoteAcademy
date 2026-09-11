"""Geometric segmentation of structured (non-MCQ) papers.

Fixtures are synthesised PDFs on the same indent bands a real CAIE structured
paper uses: a bold question number in the gutter, a bold "(a)" past that, a
bold "(i)" past that. Regular body text sits wherever, since level is decided
by the label's own x0 and boldness, never by the text next to it.
"""

from pathlib import Path

import pymupdf

from noteacademy_pipeline.segment_structured import (
    find_markers,
    segment_structured_paper,
    validate_items,
)

QUESTION_X = 50.0
PART_X = 65.0
SUBPART_X = 90.0
BODY_X = 130.0


def _insert_label(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    page.insert_text((x, y), text, fontsize=11, fontname="Helvetica-Bold")


def _insert_body(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    page.insert_text((x, y), text, fontsize=11)


def build_simple_paper(tmp_path: Path) -> Path:
    """One question, two parts, each with one sub-part — all on one page."""
    doc = pymupdf.open()
    page = doc.new_page(width=595.0, height=842.0)
    _insert_label(page, QUESTION_X, 100.0, "1")
    _insert_body(page, BODY_X, 100.0, "Fig. 1.1 shows a thing.")
    _insert_label(page, PART_X, 130.0, "(a)")
    _insert_label(page, SUBPART_X, 160.0, "(i)")
    _insert_body(page, BODY_X, 160.0, "State the thing. ....... [1]")
    _insert_label(page, PART_X, 200.0, "(b)")
    _insert_body(page, BODY_X, 200.0, "Explain the thing. ....... [2]")
    out = tmp_path / "structured.pdf"
    doc.save(out)
    doc.close()
    return out


class TestFindMarkers:
    def test_finds_one_marker_per_level(self, tmp_path):
        path = build_simple_paper(tmp_path)
        with pymupdf.open(path) as doc:
            markers, breaks = find_markers(doc[0])
        assert [(m.level, m.label) for m in markers] == [
            (0, "1"),
            (1, "a"),
            (2, "i"),
            (1, "b"),
        ]
        assert breaks == []

    def test_merged_question_and_part_label_yields_two_markers(self, tmp_path):
        # CAIE sometimes sets "10 (a)" as one span when a question's first
        # part opens on its very own line — the label the QP prints for real
        # on the first page of a fresh top-level question.
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "10 (a) ")
        _insert_body(page, BODY_X, 100.0, "State the thing.")
        out = tmp_path / "merged.pdf"
        doc.save(out)
        doc.close()

        with pymupdf.open(out) as doc:
            markers, _ = find_markers(doc[0])
        assert [(m.level, m.label) for m in markers] == [(0, "10"), (1, "a")]

    def test_merged_part_and_subpart_label_yields_two_markers(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "2")
        _insert_label(page, PART_X, 130.0, "(a) (i) ")
        _insert_body(page, BODY_X, 130.0, "State the thing.")
        out = tmp_path / "merged-part.pdf"
        doc.save(out)
        doc.close()

        with pymupdf.open(out) as doc:
            markers, _ = find_markers(doc[0])
        assert [(m.level, m.label) for m in markers] == [(0, "2"), (1, "a"), (2, "i")]

    def test_ignores_a_section_heading_but_still_records_it(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "1")
        page.insert_text((260.0, 130.0), "Section B", fontsize=12, fontname="Helvetica-Bold")
        _insert_label(page, QUESTION_X, 160.0, "2")
        out = tmp_path / "section.pdf"
        doc.save(out)
        doc.close()

        with pymupdf.open(out) as doc:
            markers, breaks = find_markers(doc[0])
        assert [m.label for m in markers] == ["1", "2"]
        assert len(breaks) == 1

    def test_non_bold_text_in_the_gutter_is_not_a_marker(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((QUESTION_X, 100.0), "1", fontsize=11)  # not bold
        out = tmp_path / "not-bold.pdf"
        doc.save(out)
        doc.close()

        with pymupdf.open(out) as doc:
            markers, _ = find_markers(doc[0])
        assert markers == []


class TestSegmentStructuredPaper:
    def test_segments_a_whole_question_with_no_problems(self, tmp_path):
        path = build_simple_paper(tmp_path)
        items, problems = segment_structured_paper(path)
        assert problems == []
        labels = [item.display_label for item in items]
        assert labels == ["1", "1(a)", "1(a)(i)", "1(b)"]

    def test_a_container_with_no_text_of_its_own_is_not_flagged(self, tmp_path):
        # "1(a)" opens straight into "(i)" with nothing said before it — that
        # is a legitimate container, not a segmentation failure.
        path = build_simple_paper(tmp_path)
        items, problems = segment_structured_paper(path)
        container = next(item for item in items if item.display_label == "1(a)")
        assert container.question_text == ""
        assert problems == []

    def test_a_leaf_with_no_text_is_flagged(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "1")
        _insert_label(page, PART_X, 130.0, "(a)")
        _insert_label(page, SUBPART_X, 160.0, "(i)")
        # Nothing follows "(i)" at all — a leaf with a blank region. Its own
        # label is 3 characters ("(i)"), which is why the check strips a
        # leaf's own label before measuring: otherwise the label alone would
        # clear the emptiness floor and this leaf would pass unflagged.
        out = tmp_path / "blank-leaf.pdf"
        doc.save(out)
        doc.close()

        items, problems = segment_structured_paper(out)
        assert any("1(a)(i)" in problem for problem in problems)

    def test_reads_the_trailing_mark_award(self, tmp_path):
        path = build_simple_paper(tmp_path)
        items, _ = segment_structured_paper(path)
        leaf = next(item for item in items if item.display_label == "1(b)")
        assert leaf.max_marks == 2

    def test_strips_the_dotted_answer_line(self, tmp_path):
        path = build_simple_paper(tmp_path)
        items, _ = segment_structured_paper(path)
        leaf = next(item for item in items if item.display_label == "1(a)(i)")
        assert "...." not in leaf.question_text

    def test_a_page_number_alone_in_the_gap_is_not_pulled_into_the_crop(self, tmp_path):
        # A continuation page whose only content before the next marker is the
        # running page number must not become part of this item's regions —
        # rendering it bleeds the *next* item's own opening line into view.
        doc = pymupdf.open()
        page1 = doc.new_page(width=595.0, height=842.0)
        _insert_label(page1, QUESTION_X, 700.0, "1")
        _insert_body(page1, BODY_X, 700.0, "Long question continues onto page 2.")
        page2 = doc.new_page(width=595.0, height=842.0)
        page2.insert_text((296.0, 50.0), "2", fontsize=10)  # bare page number
        _insert_label(page2, QUESTION_X, 60.0, "2")
        _insert_body(page2, BODY_X, 60.0, "Next question starts right away.")
        out = tmp_path / "page-number-gap.pdf"
        doc.save(out)
        doc.close()

        items, _ = segment_structured_paper(out)
        item_one = next(item for item in items if item.display_label == "1")
        assert [page for page, _ in item_one.regions] == [1]


class TestValidateItems:
    def test_duplicate_labels_are_reported(self):
        from noteacademy_pipeline.segment_structured import StructuredItem

        items = [
            StructuredItem("1", None, 0, "text", None, []),
            StructuredItem("1", None, 0, "text", None, []),
        ]
        problems = validate_items(items)
        assert any("duplicate" in problem for problem in problems)

    def test_a_short_but_real_answer_is_not_flagged(self):
        # "(i) P, ....." names a component with a single letter — real
        # content, just shorter than a part's own label plus punctuation.
        from noteacademy_pipeline.segment_structured import StructuredItem

        items = [StructuredItem("1(a)(i)", "1(a)", 2, "P,", None, [])]
        assert validate_items(items) == []


class TestAlternativeQuestions:
    """CAIE occasionally offers a straight choice between two ways of
    answering the same question — "EITHER ... OR ..." — rather than making
    the choice its own question. See _ALTERNATIVE_LABELS in
    segment_structured.py for the two shapes this takes on a real paper."""

    def build_whole_question_branch(self, tmp_path: Path) -> Path:
        """EITHER/OR right under the bare question number — each side
        carries its own full (a)/(b) of parts, sharing nothing."""
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "8")
        _insert_label(page, PART_X, 130.0, "EITHER")
        _insert_label(page, PART_X, 160.0, "(a)")
        _insert_body(page, BODY_X, 160.0, "Name the magnet's metal. [1]")
        _insert_label(page, PART_X, 200.0, "(b)")
        _insert_body(page, BODY_X, 200.0, "Explain the reading. [2]")
        _insert_label(page, PART_X, 240.0, "OR")
        _insert_label(page, PART_X, 270.0, "(a)")
        _insert_body(page, BODY_X, 270.0, "Name the gate. [1]")
        out = tmp_path / "whole-branch.pdf"
        doc.save(out)
        doc.close()
        return out

    def test_whole_question_branch_nests_parts_inside_each_side(self, tmp_path):
        path = self.build_whole_question_branch(tmp_path)
        items, problems = segment_structured_paper(path)
        assert problems == []
        labels = [item.display_label for item in items]
        assert labels == [
            "8",
            "8(either)",
            "8(either)(a)",
            "8(either)(b)",
            "8(or)",
            "8(or)(a)",
        ]

    def test_whole_question_branch_children_share_no_labels(self, tmp_path):
        # Each side gets its own "(a)" — real duplicate-part-letter
        # collision before EITHER/OR support, since both branches reuse the
        # question paper's own lettering.
        path = self.build_whole_question_branch(tmp_path)
        _, problems = segment_structured_paper(path)
        assert not any("duplicate" in problem for problem in problems)

    def test_mid_part_branch_nests_inside_the_part_it_answers(self, tmp_path):
        # (a) is shared; only (b) offers a choice, each side a single leaf
        # with no parts of its own — see the "5054_s19" case in
        # segment_structured.py's own docstring.
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "8")
        _insert_label(page, PART_X, 130.0, "(a)")
        _insert_body(page, BODY_X, 130.0, "Calculate the p.d. [2]")
        _insert_label(page, PART_X, 170.0, "(b)")
        _insert_body(page, BODY_X, 170.0, "The circuit can be adapted.")
        _insert_label(page, SUBPART_X, 200.0, "EITHER")
        _insert_body(page, BODY_X, 200.0, "Describe the relay. [2]")
        _insert_label(page, SUBPART_X, 240.0, "OR")
        _insert_body(page, BODY_X, 240.0, "Describe the transistor. [2]")
        out = tmp_path / "mid-branch.pdf"
        doc.save(out)
        doc.close()

        items, problems = segment_structured_paper(out)
        assert problems == []
        labels = [item.display_label for item in items]
        assert labels == ["8", "8(a)", "8(b)", "8(b)(either)", "8(b)(or)"]

    def test_either_merged_with_its_own_opening_text_is_still_a_marker(self, tmp_path):
        # CAIE sometimes runs the option's own text on from "EITHER" in the
        # same bold span, the way a question's first part is sometimes set.
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "7")
        _insert_label(page, PART_X, 130.0, "(a)")
        _insert_body(page, BODY_X, 130.0, "Calculate the current. [2]")
        _insert_label(page, SUBPART_X, 160.0, "EITHER  Explain the capacitor. [2]")
        _insert_label(page, SUBPART_X, 200.0, "OR")
        _insert_body(page, BODY_X, 200.0, "Explain the transistor. [2]")
        out = tmp_path / "merged-either.pdf"
        doc.save(out)
        doc.close()

        items, problems = segment_structured_paper(out)
        assert problems == []
        labels = [item.display_label for item in items]
        assert "7(a)(either)" in labels
        assert "7(a)(or)" in labels
        leaf = next(item for item in items if item.display_label == "7(a)(either)")
        assert leaf.question_text.startswith("Explain the capacitor")

    def test_or_without_a_preceding_either_is_a_problem(self, tmp_path):
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        _insert_label(page, QUESTION_X, 100.0, "1")
        _insert_label(page, PART_X, 130.0, "OR")
        _insert_body(page, BODY_X, 130.0, "Explain the thing. [1]")
        out = tmp_path / "bare-or.pdf"
        doc.save(out)
        doc.close()

        _, problems = segment_structured_paper(out)
        assert any("without a preceding EITHER" in problem for problem in problems)
