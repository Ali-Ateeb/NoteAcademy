"""classify_bbox: does the stored crop hide something the recomputed one
would show?

Built from the real incident this module exists to catch (missing_option)
and the near-miss the corpus-wide re-check also turned up (midline_cut) --
see crop_audit.py's own docstring and segment.py's fix from 2026-09-10.
"""

import pymupdf

from noteacademy_pipeline.crop_audit import classify_bbox


def _mcq_page() -> pymupdf.Page:
    """A minimal MCQ: a stem, then four options one per line.

    At 11pt, `insert_text`'s y is the baseline; each line's actual span runs
    from about y-11.8 to y+3.3 (measured, not assumed) — the boundaries below
    are chosen against those real coordinates, not the baselines themselves.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595.0, height=842.0)
    page.insert_text((72.3, 100.0), "What is the answer?", fontsize=11)
    page.insert_text((72.3, 130.0), "A first option", fontsize=11)
    page.insert_text((72.3, 150.0), "B second option", fontsize=11)
    page.insert_text((72.3, 170.0), "C third option", fontsize=11)   # span y: 158.2-173.3
    page.insert_text((72.3, 190.0), "D fourth option", fontsize=11)  # span y: 178.2-193.3
    return page


class TestClassifyBbox:
    def test_a_crop_that_already_shows_every_option_is_not_a_suspect(self):
        page = _mcq_page()
        bbox = (45.0, 90.0, 555.0, 205.0)  # comfortably past the last line
        assert classify_bbox(page, bbox, bbox) is None

    def test_missing_option_when_the_stored_box_stops_before_option_d(self):
        page = _mcq_page()
        # C's line ends at y=173.3; D's starts at y=178.2 — 176.0 clears the
        # former and falls entirely short of the latter, so D is not merely
        # cut, it is wholly outside the box.
        old_bbox = (45.0, 90.0, 555.0, 176.0)
        new_bbox = (45.0, 90.0, 555.0, 205.0)
        result = classify_bbox(page, old_bbox, new_bbox)
        assert result is not None
        kind, detail = result
        assert kind == "missing_option"
        assert "D" in detail

    def test_missing_option_when_the_stored_box_shows_zero_options(self):
        """The cube-weight case: the stem alone is captured, no option letter
        at all -- the most severe form of the same bug."""
        page = _mcq_page()
        old_bbox = (45.0, 90.0, 555.0, 115.0)  # stem only
        new_bbox = (45.0, 90.0, 555.0, 205.0)
        kind, detail = classify_bbox(page, old_bbox, new_bbox)
        assert kind == "missing_option"
        assert "no options" in detail

    def test_midline_cut_when_option_d_is_sliced_rather_than_absent(self):
        """Option D's own letter renders just inside the stored box (so its
        letter is already "found"), but the box's bottom edge falls in the
        middle of that same line -- the harder case to catch, and the one
        the bare-letter heuristic alone would miss."""
        page = _mcq_page()
        # The "D fourth option" span's bbox runs y=178.2 to y=193.3; 185.0
        # falls inside that range, well clear of either edge.
        old_bbox = (45.0, 90.0, 555.0, 185.0)
        new_bbox = (45.0, 90.0, 555.0, 205.0)
        result = classify_bbox(page, old_bbox, new_bbox)
        assert result is not None
        kind, _detail = result
        assert kind == "midline_cut"

    def test_a_shrunk_box_that_still_shows_everything_is_not_a_suspect(self):
        """The recomputed box is *smaller* -- excess whitespace or a footer
        artefact correctly trimmed away, per segment.py's own 2026-09-10 fix.
        Nothing is hidden from a student by less blank space, so this must
        not be reported."""
        page = _mcq_page()
        old_bbox = (45.0, 90.0, 555.0, 400.0)  # generous, includes lots of blank paper
        new_bbox = (45.0, 90.0, 555.0, 205.0)  # tightened, still shows all four options
        assert classify_bbox(page, old_bbox, new_bbox) is None

    def test_a_diagram_only_question_with_no_text_options_is_not_a_suspect(self):
        """No bare B/C/D token anywhere -- the heuristic has nothing to go
        on, which must read as "cannot confirm a problem", not as one."""
        doc = pymupdf.open()
        page = doc.new_page(width=595.0, height=842.0)
        page.insert_text((72.3, 100.0), "A diagram is shown above.", fontsize=11)
        old_bbox = (45.0, 90.0, 555.0, 115.0)
        new_bbox = (45.0, 90.0, 555.0, 130.0)
        assert classify_bbox(page, old_bbox, new_bbox) is None
