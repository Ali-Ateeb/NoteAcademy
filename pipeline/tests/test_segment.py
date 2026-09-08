"""Geometric segmentation of multiple-choice papers.

Fixtures are synthesised PDFs laid out on the same grid CAIE uses — question
number alone in a left gutter at x≈50, everything else indented past x≈72 — so
the geometric rule is tested without redistributing a real paper.
"""

from pathlib import Path

import pymupdf

from noteacademy_pipeline.segment import (
    QuestionRegion,
    find_question_starts,
    segment_mcq_paper,
    validate_regions,
)


def build_paper(
    tmp_path: Path,
    pages: list[list[tuple[int, float]]],
    *,
    width: float = 595.0,
    height: float = 842.0,
) -> Path:
    """A PDF whose pages carry question numbers in the gutter at given heights."""
    doc = pymupdf.open()
    for questions in pages:
        page = doc.new_page(width=width, height=height)
        # Page furniture: running page number at the top, footer at the bottom.
        page.insert_text((296, 34), "3", fontsize=10)
        page.insert_text((50, 795), "06_1234_11_2026  © 2026  12", fontsize=8)
        for number, y in questions:
            page.insert_text((49.6, y), str(number), fontsize=11)   # gutter
            page.insert_text((72.3, y), "Stem text 10 20 30", fontsize=11)  # indented
    out = tmp_path / "paper.pdf"
    doc.save(out)
    doc.close()
    return out


class TestFindQuestionStarts:
    def test_finds_gutter_numbers_and_ignores_indented_ones(self, tmp_path):
        path = build_paper(tmp_path, [[(1, 100.0), (2, 300.0)]])
        with pymupdf.open(path) as doc:
            starts = find_question_starts(doc[0])
        # "10 20 30" sits in the stem at x=72 and must not be read as questions.
        assert [n for n, _ in starts] == [1, 2]

    def test_ignores_the_running_page_number_and_footer(self, tmp_path):
        path = build_paper(tmp_path, [[(7, 200.0)]])
        with pymupdf.open(path) as doc:
            starts = find_question_starts(doc[0])
        assert [n for n, _ in starts] == [7]

    def test_returns_starts_in_page_order(self, tmp_path):
        path = build_paper(tmp_path, [[(2, 400.0), (1, 100.0)]])
        with pymupdf.open(path) as doc:
            starts = find_question_starts(doc[0])
        assert [y for _, y in starts] == sorted(y for _, y in starts)


class TestSegmentMcqPaper:
    def test_segments_a_whole_paper(self, tmp_path):
        path = build_paper(
            tmp_path,
            [[(1, 100.0), (2, 400.0)], [(3, 100.0), (4, 400.0)]],
        )
        regions, problems = segment_mcq_paper(path)
        assert problems == []
        assert [r.number for r in regions] == [1, 2, 3, 4]
        assert [r.page_number for r in regions] == [1, 1, 2, 2]

    def test_a_question_stops_before_the_next_one(self, tmp_path):
        path = build_paper(tmp_path, [[(1, 100.0), (2, 400.0)]])
        regions, _ = segment_mcq_paper(path)
        assert regions[0].bbox[3] <= regions[1].bbox[1] + 1

    def test_reports_a_paper_that_is_not_the_expected_template(self, tmp_path):
        # US Letter, or a scan cropped to a different size. Better to refuse and
        # route it to the vision path than to emit confidently wrong boxes.
        path = build_paper(tmp_path, [[(1, 100.0)]], width=612.0, height=792.0)
        _, problems = segment_mcq_paper(path)
        assert any("template" in p for p in problems)

    def test_reports_a_paper_with_no_questions(self, tmp_path):
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        path = tmp_path / "blank.pdf"
        doc.save(path)
        doc.close()
        _, problems = segment_mcq_paper(path)
        assert any("no questions" in p for p in problems)


class TestValidateRegions:
    def region(self, number: int) -> QuestionRegion:
        return QuestionRegion(number=number, page_number=1, bbox=(45, 100, 555, 200))

    def test_a_contiguous_set_is_clean(self):
        assert validate_regions([self.region(n) for n in range(1, 41)]) == []

    def test_reports_a_missing_question(self):
        regions = [self.region(n) for n in range(1, 41) if n != 19]
        assert any("19" in p for p in validate_regions(regions))

    def test_reports_a_duplicate(self):
        regions = [self.region(1), self.region(2), self.region(2)]
        assert any("duplicate" in p for p in validate_regions(regions))

    def test_reports_numbers_out_of_order(self):
        regions = [self.region(3), self.region(1), self.region(2)]
        assert any("ascending" in p for p in validate_regions(regions))

    def test_reports_a_degenerate_box(self):
        bad = QuestionRegion(number=1, page_number=1, bbox=(45, 300, 555, 100))
        assert any("degenerate" in p for p in validate_regions([bad]))
