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

    def test_the_copyright_notice_is_not_part_of_the_last_question(self, tmp_path):
        """The last question on the last page must not swallow the footer.

        CAIE closes a paper with a hairline rule and several lines of copyright
        acknowledgement at 7pt. Both are ink, so trimming the crop to "where the
        ink stops" ran it to the bottom of the page: the student is shown a
        paragraph of legal boilerplate under the question, and the text index
        for that question *is* the boilerplate.
        """
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        # Numbered 1 because validate_regions requires a contiguous 1..n; the
        # point here is that it is the last question *on its page*.
        page.insert_text((49.6, 100.0), "1", fontsize=11)
        page.insert_text((72.3, 100.0), "The last question", fontsize=11)
        page.insert_text((72.3, 130.0), "D 3 minutes", fontsize=11)
        # The footer: separator rule, then the notice, both well below the last
        # option and both unmistakably page furniture.
        page.draw_line((48.1, 729.6), (547.2, 729.6), width=0.8)
        for i, y in enumerate((740.5, 748.6, 756.6)):
            page.insert_text((50, y), f"Permission to reproduce items {i}", fontsize=7)
        path = tmp_path / "last-page.pdf"
        doc.save(path)
        doc.close()

        regions, problems = segment_mcq_paper(path)
        assert problems == []
        assert len(regions) == 1
        # Ends just after the final option, not down in the notice.
        assert regions[0].bbox[3] < 200.0

    def test_the_last_option_is_not_cut_off_when_it_sits_close_to_the_body_edge(
        self, tmp_path
    ):
        """A diagram (or a wide table) can push a last question's options down.

        Found in production on a physics question whose cube-weight diagram
        pushed its four options to y~764-777, just inside BODY_BOTTOM (780):
        the old code computed the last-question ceiling as
        `BODY_BOTTOM - CROP_GAP` (772), so the option row fell entirely past
        it and `content_bottom` never saw it, trimming the crop to whatever
        ended above the missing option instead.
        """
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((49.6, 100.0), "1", fontsize=11)
        page.insert_text((72.3, 100.0), "The last question", fontsize=11)
        # The final option sits at y=764, comfortably inside BODY_BOTTOM
        # (780) but past BODY_BOTTOM - CROP_GAP (772).
        page.insert_text((72.3, 764.0), "D 3 minutes", fontsize=11)
        page.insert_text((47.3, 797.4), "© UCLES 2026", fontsize=8)
        page.insert_text((269.4, 797.4), "5054/11/O/N/26", fontsize=8)
        path = tmp_path / "tight-last-option.pdf"
        doc.save(path)
        doc.close()

        regions, problems = segment_mcq_paper(path)
        assert problems == []
        assert len(regions) == 1
        # Bottom edge must clear the option's own baseline, not stop at 772.
        assert regions[0].bbox[3] > 775.0

    def test_turn_over_is_not_mistaken_for_the_last_question_s_content(self, tmp_path):
        """"[Turn over" is set at body-text size, so a size-only footer test misses it.

        Letting it count as ink pulled the crop down to include it — page
        furniture, not part of the question.
        """
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((49.6, 100.0), "1", fontsize=11)
        page.insert_text((72.3, 100.0), "The last question", fontsize=11)
        page.insert_text((72.3, 130.0), "D 3 minutes", fontsize=11)
        # Printed at body-text size, well clear of FOOTER_MAX_SIZE.
        page.insert_text((468.1, 765.0), "[Turn over", fontsize=11)
        path = tmp_path / "turn-over.pdf"
        doc.save(path)
        doc.close()

        regions, problems = segment_mcq_paper(path)
        assert problems == []
        assert len(regions) == 1
        assert regions[0].bbox[3] < 200.0

    def test_an_invisible_spacing_glyph_does_not_stretch_the_crop(self, tmp_path):
        """A blank glyph set to an oversized font must not count as content.

        Some CAIE PDFs carry one purely to push layout around in whatever
        tool produced the file. It has a real bounding box but nothing a
        student would see, so it must not count as "ink" when trimming the
        last crop on a page.
        """
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((49.6, 100.0), "1", fontsize=11)
        page.insert_text((72.3, 100.0), "The last question", fontsize=11)
        page.insert_text((72.3, 130.0), "D 3 minutes", fontsize=11)
        # A blank space set at a huge font size, far below the real content.
        page.insert_text((42.8, 700.0), " ", fontsize=50)
        path = tmp_path / "invisible-glyph.pdf"
        doc.save(path)
        doc.close()

        regions, problems = segment_mcq_paper(path)
        assert problems == []
        assert len(regions) == 1
        assert regions[0].bbox[3] < 200.0

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
