"""The checks that run before an ingestion writes anything."""

from __future__ import annotations

from noteacademy_pipeline.ingest import disagreements, file_digest
from noteacademy_pipeline.naming import parse_paper_filename


def pair(qp: str, ms: str):
    return parse_paper_filename(qp), parse_paper_filename(ms)


def test_a_matching_pair_has_nothing_to_say():
    assert disagreements(*pair("5054_s19_qp_11", "5054_s19_ms_11")) == []


def test_catches_the_wrong_variant():
    # The dangerous one: same paper, same session, different question set. Every
    # answer it supplies is a plausible letter, and most of them are wrong.
    assert disagreements(*pair("5054_s19_qp_11", "5054_s19_ms_12")) == ["variant"]


def test_catches_the_wrong_session():
    problems = disagreements(*pair("5054_s19_qp_11", "5054_w19_ms_11"))
    assert problems == ["season"]


def test_catches_the_wrong_subject_entirely():
    problems = disagreements(*pair("5054_s19_qp_11", "5070_s19_ms_11"))
    assert problems == ["syllabus"]


def test_catches_two_question_papers():
    problems = disagreements(*pair("5054_s19_qp_11", "5054_s19_qp_11"))
    assert problems == ["qp given where a mark scheme was expected"]


def test_digest_distinguishes_files(tmp_path):
    one, two = tmp_path / "a.pdf", tmp_path / "b.pdf"
    one.write_bytes(b"%PDF-1.7 one")
    two.write_bytes(b"%PDF-1.7 two")

    assert file_digest(one) == file_digest(one)
    assert file_digest(one) != file_digest(two)
    assert len(file_digest(one)) == 64
