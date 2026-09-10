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
