"""First-pass MCQ tagging: what is sent, what is skipped, what is written."""

from __future__ import annotations

import dataclasses

import pytest

from noteacademy_pipeline import tag_auto
from noteacademy_pipeline.deepseek import DeepSeekAccountError
from noteacademy_pipeline.schemas import TopicAssignment, TopicTagging
from noteacademy_pipeline.tag_auto import (
    UntaggedMcq,
    render_question,
    tag_mcqs_with_model,
)
from noteacademy_pipeline.worksheet import ApplyReport


def mcq(n, text="Which oxide is amphoteric?", options=None, correct="A") -> UntaggedMcq:
    options = {"A": "Al2O3", "B": "CO2", "C": "Na2O", "D": "SO2"} if options is None else options
    return UntaggedMcq(id=f"q{n}", paper_slug="chem-2016", display_label=str(n),
                       question_text=text, options=options, correct_option=correct)


class _Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()).lower(), params))


class _Conn:
    def __init__(self):
        self.committed = self.rolled_back = self.closed = False
        self.sql = []

    def cursor(self):
        return _Cursor(self.sql)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def world(monkeypatch):
    state = {"questions": [], "conns": [], "sent": [], "decisions": None, "raise": None,
             "code": "8.1", "confidence": 0.9}

    monkeypatch.setattr("noteacademy_pipeline.load.connect",
                        lambda url: (state["conns"].append(_Conn()) or state["conns"][-1]))
    monkeypatch.setattr(tag_auto, "revisable_topics",
                        lambda conn, slug: ("v", [{"code": "8.1", "title": "T",
                                                   "learning_objectives": ["o"]}]))
    monkeypatch.setattr(tag_auto, "load_untagged_mcqs", lambda conn, slug, **k: state["questions"])

    def fake_tag(text, topics, *, mark_scheme=None):
        state["sent"].append((text, mark_scheme))
        if state["raise"]:
            raise state["raise"]
        return TopicTagging(
            primary=TopicAssignment(topic_code=state["code"], confidence=state["confidence"],
                                    reasoning="r"), secondary=[])

    def fake_apply(conn, slug, decisions, *, confidence_floor):
        state["decisions"] = decisions
        held = sum(1 for d in decisions if d["confidence"] < confidence_floor)
        return ApplyReport(tagged=len(decisions), flagged=held)

    monkeypatch.setattr(tag_auto, "tag_question", fake_tag)
    monkeypatch.setattr(tag_auto, "apply_worksheet", fake_apply)
    return state


def run(**kw):
    return tag_mcqs_with_model("chemistry-5070", year_from=2016, year_to=2026,
                               confidence_floor=0.75, **kw)


def test_the_stem_options_and_keyed_answer_are_what_the_model_sees(world):
    world["questions"] = [mcq(1)]
    run()
    text, scheme = world["sent"][0]
    assert text == "Which oxide is amphoteric?\nA. Al2O3\nB. CO2\nC. Na2O\nD. SO2"
    assert scheme == "Correct answer: A. Al2O3"


def test_a_question_with_no_text_at_all_is_skipped_not_guessed_at(world):
    world["questions"] = [mcq(1), mcq(2, text="", options={})]
    report = run()
    assert report.skipped_no_text == 1 and len(world["sent"]) == 1
    assert [d["id"] for d in world["decisions"]] == ["q1"]


def test_options_alone_are_enough_to_classify(world):
    world["questions"] = [mcq(1, text="")]
    assert run().skipped_no_text == 0
    assert world["sent"][0][0].startswith("A. ")


def test_no_keyed_answer_means_no_mark_scheme():
    assert tag_auto._mark_scheme(mcq(1, correct=None)) is None


def test_render_orders_options_by_letter():
    q = mcq(1, options={"C": "c", "A": "a"})
    assert render_question(q).splitlines()[1:] == ["A. a", "C. c"]


def test_low_confidence_is_held_and_the_spread_is_counted(world):
    world["questions"] = [mcq(1), mcq(2)]
    world["confidence"] = 0.5
    report = run()
    assert (report.tagged, report.held_for_review) == (2, 2)
    assert report.by_topic == {"8.1": 2}


def test_a_dry_run_rolls_back_and_a_real_run_commits(world):
    world["questions"] = [mcq(1)]
    run(dry_run=True)
    assert world["conns"][-1].rolled_back and not world["conns"][-1].committed
    run(dry_run=False)
    assert world["conns"][-1].committed


def test_a_rejected_key_aborts_before_any_write(world):
    world["questions"] = [mcq(i) for i in range(4)]
    world["raise"] = DeepSeekAccountError("out of balance (402)")
    with pytest.raises(DeepSeekAccountError):
        run(workers=3)
    assert len(world["conns"]) == 1 and world["decisions"] is None  # only the read connection


def test_one_failed_call_is_reported_and_does_not_lose_the_rest(world, monkeypatch):
    world["questions"] = [mcq(1), mcq(2)]
    calls = {"n": 0}
    original = tag_auto.tag_question

    def flaky(text, topics, *, mark_scheme=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("bad json")
        return original(text, topics, mark_scheme=mark_scheme)

    monkeypatch.setattr(tag_auto, "tag_question", flaky)
    report = run()
    assert report.failed_calls == ["chem-2016 Q1"]
    assert [d["id"] for d in world["decisions"]] == ["q2"]


def test_retag_is_refused_without_a_named_paper(world):
    with pytest.raises(ValueError, match="paper_slug"):
        run(retag=True)
    assert world["conns"] == []  # refused before anything was opened


def test_retag_replaces_old_tags_and_returns_the_questions_to_review(world, monkeypatch, tmp_path):
    world["questions"] = [mcq(1), mcq(2)]
    monkeypatch.setattr(tag_auto, "_current_tags", lambda conn, ids: {"q1": "8.1", "q2": "9.9"})
    monkeypatch.setattr(tag_auto, "settings",
                        dataclasses.replace(tag_auto.settings, work_dir=tmp_path))
    report = run(retag=True, paper_slug="chem-2016")

    write_conn = world["conns"][-1]
    statements = [sql for sql, _ in write_conn.sql]
    assert statements[0].startswith("delete from question_topics")
    assert "needs_review" in statements[1]
    assert (report.retagged, report.changed_topic) == (2, 1)   # q1 was already 8.1
    assert report.backup is not None
    assert len(report.backup.read_text(encoding="utf-8").splitlines()) == 3  # header + 2 rows


# --------------------------------------------------------------------------
# tagging the questions that have no text at all, from their printed crop
# --------------------------------------------------------------------------


def textless(n, **kw) -> UntaggedMcq:
    base = dict(id=f"q{n}", paper_slug="physics-5054-2020-may-june-p11", display_label=str(n),
                question_text="", options={}, correct_option="A",
                syllabus_code="5054", year=2020, season="may_june", component=1, variant=1,
                page_number=3, bbox=(0.0, 0.0, 100.0, 100.0))
    base.update(kw)
    return UntaggedMcq(**base)


@pytest.fixture
def crop_world(world, monkeypatch, tmp_path):
    """Extends `world` with a fake renderer and a fake vision call."""
    state = world
    state["cropped"] = []
    state["crop_confidence"] = 0.95

    def fake_crop(pdf, page, bbox, out, **kw):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")
        state["cropped"].append(out.name)
        return out

    def fake_tag_from_crop(path, topics, *, thinking=None):
        state["sent"].append((path.name, thinking))
        if state["raise"]:
            raise state["raise"]
        return TopicAssignment(topic_code=state["code"], confidence=state["crop_confidence"],
                               reasoning="r")

    monkeypatch.setattr("noteacademy_pipeline.render.crop", fake_crop)
    monkeypatch.setattr("noteacademy_pipeline.tagging.tag_from_crop", fake_tag_from_crop)
    papers = tmp_path / "papers" / "5054"
    papers.mkdir(parents=True)
    (papers / "5054_s20_qp_11.pdf").write_bytes(b"%PDF")
    state["papers"] = tmp_path / "papers"
    state["crop_dir"] = tmp_path / "crops"
    return state


def run_crops(world, **kw):
    from noteacademy_pipeline.tag_auto import tag_mcqs_from_crops

    kw.setdefault("crop_dir", world["crop_dir"])
    return tag_mcqs_from_crops(
        "physics-5054", world["papers"], year_from=2016, year_to=2026,
        confidence_floor=0.75, **kw,
    )


def test_only_the_questions_with_no_text_are_read_from_their_crop(crop_world):
    crop_world["questions"] = [textless(1), mcq(2)]      # mcq(2) has text and options
    report = run_crops(crop_world)
    assert report.candidates == 1
    assert [d["id"] for d in crop_world["decisions"]] == ["q1"]


def test_the_tag_is_held_below_the_floor_however_confident_the_model_sounds(crop_world):
    crop_world["questions"] = [textless(1)]
    crop_world["crop_confidence"] = 0.95
    run_crops(crop_world)
    assert crop_world["decisions"][0]["confidence"] < 0.75


def test_a_question_with_no_recorded_crop_is_counted_not_guessed_at(crop_world):
    crop_world["questions"] = [textless(1, bbox=None, page_number=None)]
    report = run_crops(crop_world)
    assert report.skipped_no_crop == 1
    assert crop_world["sent"] == []


def test_a_missing_source_pdf_is_counted_not_guessed_at(crop_world):
    crop_world["questions"] = [textless(1, year=2011)]   # no 5054_s11_qp_11.pdf on disk
    report = run_crops(crop_world)
    assert report.skipped_no_pdf == 1
    assert crop_world["sent"] == []


def test_no_connection_is_open_while_the_crop_is_being_read(crop_world):
    crop_world["questions"] = [textless(1)]
    run_crops(crop_world)
    # One connection to read the plan, one to write: never one spanning the call.
    assert len(crop_world["conns"]) == 2
    assert crop_world["conns"][0].closed


def test_a_rejected_key_stops_the_crop_run_before_any_write(crop_world):
    crop_world["questions"] = [textless(i) for i in range(4)]
    crop_world["raise"] = DeepSeekAccountError("out of balance (402)")
    with pytest.raises(DeepSeekAccountError):
        run_crops(crop_world, workers=2)
    assert len(crop_world["conns"]) == 1 and crop_world["decisions"] is None
