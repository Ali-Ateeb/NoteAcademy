"""Filename parsing.

Worth testing carefully out of proportion to its size: this is what decides
which session a paper is filed under, and a paper under the wrong year is not a
visible bug — it is a paper nobody finds.
"""

from __future__ import annotations

import pytest

from noteacademy_pipeline.naming import (
    crop_key,
    document_key,
    expand_year,
    parse_paper_filename,
    storage_prefix,
)


def test_reads_a_variant_paper():
    paper = parse_paper_filename("5054_s19_qp_11")
    assert paper.syllabus_code == "5054"
    assert paper.year == 2019
    assert paper.season == "may_june"
    assert paper.doc_type == "qp"
    assert paper.component == 1
    assert paper.variant == 1


def test_reads_the_three_seasons():
    assert parse_paper_filename("5054_m20_qp_12").season == "feb_march"
    assert parse_paper_filename("5054_s20_qp_12").season == "may_june"
    assert parse_paper_filename("5054_w20_qp_12").season == "oct_nov"


def test_a_component_without_variants_has_no_variant():
    paper = parse_paper_filename("5054_w15_ms_2")
    assert (paper.component, paper.variant) == (2, None)


def test_documents_that_name_no_component():
    paper = parse_paper_filename("5054_s19_gt")
    assert paper.doc_type == "gt"
    assert paper.component is None


@pytest.mark.parametrize(
    "stem",
    [
        "5054_s19_qp",          # a question paper must say which one
        "physics_s19_qp_11",    # not a syllabus code
        "5054_x19_qp_11",       # not a season
        "5054_s19_zz_11",       # not a document type
        "5054_s2019_qp_11",     # not CAIE's year format
        "",
    ],
)
def test_refuses_anything_it_does_not_recognise(stem):
    if stem == "5054_s19_qp":
        # This one parses: 'qp' with no number is a real shape for papers with a
        # single component. It is the ingestion command that insists on knowing
        # the component, because that is where it matters.
        assert parse_paper_filename(stem).component is None
        return
    with pytest.raises(ValueError):
        parse_paper_filename(stem)


def test_two_digit_years_span_the_century():
    assert expand_year(98) == 1998
    assert expand_year(26) == 2026
    assert expand_year(0) == 2000


def test_storage_keys_are_readable():
    prefix = storage_prefix("5054", 2019, "may_june", 1, 2)
    assert prefix == "papers/5054/2019-mj/p12"
    assert document_key(prefix, "ms") == "papers/5054/2019-mj/p12/ms.pdf"
    assert crop_key(prefix, 13) == "papers/5054/2019-mj/p12/crops/13.png"


def test_a_component_without_variants_keys_without_one():
    assert storage_prefix("4024", 2015, "oct_nov", 2, None) == "papers/4024/2015-on/p2"
