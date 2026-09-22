"""The second-pass tagging pipeline's pure logic.

Building the sheets and diffing the two passes both touch a database and a
PDF, so what is tested here is what does not need either: the bin-packing that
lays a sheet out, the text normalisation duplicates are matched on, and the
crop-path resolver's two directory-naming conventions.
"""

from __future__ import annotations

from noteacademy_pipeline.verify import (
    TaggedQuestion,
    _local_crop_path,
    _normalise,
    pack_by_height,
)


class TestPackByHeight:
    def test_packs_items_that_fit_together(self):
        assert pack_by_height([100, 100, 100], max_total=1000) == [[0, 1, 2]]

    def test_starts_a_new_bin_when_the_next_item_would_overflow(self):
        assert pack_by_height([600, 600], max_total=1000) == [[0], [1]]

    def test_an_oversized_item_gets_a_bin_of_its_own_rather_than_vanishing(self):
        # The failure mode worth guarding: a bin never starts empty, so a
        # single item taller than max_total is not silently dropped.
        assert pack_by_height([2000], max_total=1000) == [[0]]

    def test_a_mix_packs_greedily_in_order(self):
        assert pack_by_height([300, 300, 300, 300], max_total=700) == [
            [0, 1],
            [2, 3],
        ]

    def test_empty_input_produces_no_bins(self):
        assert pack_by_height([], max_total=1000) == []


class TestNormalise:
    def test_folds_case_and_punctuation(self):
        assert _normalise("Which quantity, X, is a Vector?") == \
            "which quantity x is a vector"

    def test_folds_accents(self):
        assert _normalise("Réfraction") == "refraction"

    def test_strips_the_questions_own_printed_number(self):
        # The bug this rule exists for: the same question renumbered between
        # two CAIE variants of one sitting compared unequal on the label
        # alone, even though the content after it was identical.
        assert _normalise("3 A student measures the speed of a trolley") == \
            _normalise("4 A student measures the speed of a trolley")

    def test_does_not_strip_a_number_that_is_part_of_the_stem(self):
        # Only the leading item number goes; a real quantity right after it
        # is content, not a label, and must survive.
        assert _normalise("7 2 kg of ice is heated") == "2 kg of ice is heated"


def paper_question(**overrides) -> TaggedQuestion:
    base = dict(
        id="q1", paper_slug="physics-5054-2019-may-june-p11", display_label="7",
        ordinal=7, syllabus_code="5054", year=2019, season="may_june",
        component=1, variant=1, page_number=2, bbox=(0.0, 0.0, 1.0, 1.0),
        primary_code="1.2", primary_confidence=0.9,
    )
    base.update(overrides)
    return TaggedQuestion(**base)


class TestLocalCropPath:
    def test_finds_the_bulk_ingestion_naming(self, tmp_path):
        work = tmp_path
        (work / "5054_s19_qp_11" / "crops").mkdir(parents=True)
        crop = work / "5054_s19_qp_11" / "crops" / "7.png"
        crop.write_bytes(b"not a real png, just a marker")

        found = _local_crop_path(work, paper_question())
        assert found == crop

    def test_finds_the_no_qp_naming_used_by_the_first_three_papers(self, tmp_path):
        work = tmp_path
        (work / "5054_s19_11" / "crops").mkdir(parents=True)
        crop = work / "5054_s19_11" / "crops" / "7.png"
        crop.write_bytes(b"marker")

        found = _local_crop_path(work, paper_question())
        assert found == crop

    def test_returns_none_when_no_crop_exists_under_either_name(self, tmp_path):
        assert _local_crop_path(tmp_path, paper_question()) is None

    def test_prefers_the_qp_naming_when_both_exist(self, tmp_path):
        work = tmp_path
        (work / "5054_s19_qp_11" / "crops").mkdir(parents=True)
        (work / "5054_s19_11" / "crops").mkdir(parents=True)
        preferred = work / "5054_s19_qp_11" / "crops" / "7.png"
        preferred.write_bytes(b"marker")
        (work / "5054_s19_11" / "crops" / "7.png").write_bytes(b"other")

        assert _local_crop_path(work, paper_question()) == preferred


# --------------------------------------------------------------------------
# bulk-approve-structured reports the tag quality it is publishing
# --------------------------------------------------------------------------


class _ApproveCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        self.conn.statements.append(flat)
        if "count(*) filter" in flat:
            self.rows = [self.conn.counts]
        elif flat.startswith("select q.id, q.paper_id"):
            self.rows = [{"id": "q1", "paper_id": "p1", "display_label": "1"}]
        elif flat.startswith("select id from questions"):
            self.rows = [{"id": "q1"}, {"id": "q1a"}]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _ApproveConn:
    def __init__(self, below=0, untagged=0):
        self.statements: list[str] = []
        self.counts = {"below": below, "untagged": untagged}

    def cursor(self):
        return _ApproveCursor(self)


def test_bulk_approve_structured_counts_the_unverified_tags_it_publishes():
    from noteacademy_pipeline.verify import bulk_approve_structured

    conn = _ApproveConn(below=3, untagged=1)
    report = bulk_approve_structured(conn, "chemistry-5070", dry_run=True)
    assert (report.below_floor, report.untagged) == (3, 1)
    assert report.candidates == 1


def test_bulk_approve_structured_still_does_not_gate_on_the_tag():
    # The counts are advisory: a low-confidence tag does not hold the content back.
    from noteacademy_pipeline.verify import bulk_approve_structured

    conn = _ApproveConn(below=1)
    report = bulk_approve_structured(conn, "chemistry-5070", dry_run=False)
    assert report.approved == 1
    assert any(s.startswith("update questions set extraction_status = 'approved'")
               for s in conn.statements)
