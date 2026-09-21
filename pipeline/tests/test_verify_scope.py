"""The pieces added around the verifiers: the per-call thinking override, the
structured model call, and the year scope on the multiple-choice verifier."""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from noteacademy_pipeline import deepseek, verify
from noteacademy_pipeline.config import Settings
from noteacademy_pipeline.deepseek import DeepSeekAccountError
from noteacademy_pipeline.naming import caie_filename
from noteacademy_pipeline.schemas import TopicAssignment
from noteacademy_pipeline.tagging import (
    MAX_STRUCTURED_PAGES,
    TopicOption,
    tag_from_crop,
    tag_structured_from_crops,
)
from noteacademy_pipeline.verify import TaggedQuestion, VerifyReport, verify_mcqs_with_model

# --------------------------------------------------------------------------
# chat_json's thinking override
# --------------------------------------------------------------------------


def _body_for(monkeypatch, *, setting: bool, override: bool | None) -> dict:
    monkeypatch.setattr(
        deepseek, "settings",
        dataclasses.replace(Settings(), deepseek_api_key="k", deepseek_thinking=setting),
    )
    monkeypatch.setattr(deepseek.time, "sleep", lambda s: None)
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"},
                                                      "finish_reason": "stop"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    deepseek.chat_json(model="deepseek-flash", system="Reply as JSON.", user_content="q",
                       thinking=override, client=client)
    return json.loads(seen[0].content)


def test_thinking_none_follows_the_setting(monkeypatch):
    assert _body_for(monkeypatch, setting=False, override=None)["thinking"] == {"type": "disabled"}
    assert _body_for(monkeypatch, setting=True, override=None)["thinking"] == {"type": "enabled"}


def test_thinking_true_overrides_a_setting_that_is_off(monkeypatch):
    body = _body_for(monkeypatch, setting=False, override=True)
    assert body["thinking"] == {"type": "enabled"}
    assert body["max_tokens"] >= 16000  # room for the reasoning as well as the answer
    assert "temperature" not in body


def test_thinking_false_overrides_a_setting_that_is_on(monkeypatch):
    body = _body_for(monkeypatch, setting=True, override=False)
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0


# --------------------------------------------------------------------------
# the model calls
# --------------------------------------------------------------------------


@pytest.fixture
def calls(monkeypatch):
    """Replaces chat_json with a recorder whose reply the test chooses."""
    recorded: list[dict] = []
    reply = {"text": '{"primary": {"topic_code": "1.1", "confidence": 0.9, "reasoning": "r"}, '
                     '"secondary": []}'}

    def fake(**kwargs):
        recorded.append(kwargs)
        return reply["text"]

    monkeypatch.setattr(deepseek, "chat_json", fake)
    recorded_reply = reply
    return recorded, recorded_reply


def topics():
    return [
        TopicOption(code="1.1", title="One", learning_objectives=["a"]),
        TopicOption(code="2.1", title="Two", learning_objectives=["b"]),
    ]


def make_crops(tmp_path, n):
    paths = []
    for i in range(n):
        path = tmp_path / f"c{i}.png"
        path.write_bytes(b"png")
        paths.append(path)
    return paths


def reply_json(primary, *secondary, confidence=0.9):
    return json.dumps({
        "primary": {"topic_code": primary, "confidence": confidence, "reasoning": "r"},
        "secondary": [{"topic_code": c, "confidence": 0.6, "reasoning": "s"} for c in secondary],
    })


def test_structured_call_sends_syllabus_then_each_page_in_order_then_the_shape(calls, tmp_path):
    recorded, _ = calls
    tag_structured_from_crops(make_crops(tmp_path, 2), topics())

    content = recorded[0]["user_content"]
    assert [b["type"] for b in content] == [
        "text", "text", "image_url", "text", "image_url", "text",
    ]
    # The constant syllabus leads, so DeepSeek's prefix cache can hit.
    assert content[0]["text"].startswith("Syllabus topics:\n\n")
    assert content[1]["text"] == "Page 1 of 2 of the question:"
    assert content[3]["text"] == "Page 2 of 2 of the question:"
    assert "JSON" in content[5]["text"]                           # JSON mode wants the word


def test_structured_call_uses_the_vision_model_and_passes_thinking(calls, tmp_path, monkeypatch):
    recorded, _ = calls
    monkeypatch.setattr(
        "noteacademy_pipeline.tagging.settings",
        dataclasses.replace(Settings(), deepseek_vision_model="deepseek-flash"),
    )
    tag_structured_from_crops(make_crops(tmp_path, 1), topics(), thinking=True)

    assert recorded[0]["model"] == "deepseek-flash"
    assert recorded[0]["thinking"] is True
    assert "primary topic" in recorded[0]["system"] and "TWO secondary" in recorded[0]["system"]


def test_structured_call_does_not_show_the_model_the_mark_scheme(calls, tmp_path):
    recorded, _ = calls
    tag_structured_from_crops(make_crops(tmp_path, 1), topics())
    text = " ".join(b.get("text", "") for b in recorded[0]["user_content"])
    assert "Mark scheme" not in text


def test_a_topic_listed_as_both_primary_and_secondary_is_the_primary_only(calls, tmp_path):
    recorded, reply = calls
    reply["text"] = reply_json("1.1", "1.1", "2.1")
    result = tag_structured_from_crops(make_crops(tmp_path, 1), topics())
    assert [t.topic_code for t in result.secondary] == ["2.1"]


def test_an_invented_primary_code_is_zeroed_and_an_invented_secondary_dropped(calls, tmp_path):
    recorded, reply = calls
    reply["text"] = reply_json("9.9", "2.1", "8.8")
    result = tag_structured_from_crops(make_crops(tmp_path, 1), topics())
    assert result.primary.confidence == 0.0
    assert [t.topic_code for t in result.secondary] == ["2.1"]


def test_no_crops_or_too_many_is_refused_before_any_call(calls, tmp_path):
    recorded, _ = calls
    with pytest.raises(ValueError, match="at least one"):
        tag_structured_from_crops([], topics())
    with pytest.raises(ValueError, match="segmentation"):
        tag_structured_from_crops(make_crops(tmp_path, MAX_STRUCTURED_PAGES + 1), topics())
    assert recorded == []


def test_tag_from_crop_passes_thinking_through(calls, tmp_path):
    recorded, reply = calls
    reply["text"] = json.dumps({"topic_code": "1.1", "confidence": 0.9, "reasoning": "r"})
    tag_from_crop(make_crops(tmp_path, 1)[0], topics(), thinking=True)
    assert recorded[0]["thinking"] is True


# --------------------------------------------------------------------------
# the multiple-choice verifier's year scope
# --------------------------------------------------------------------------


class _Conn:
    def __init__(self):
        self.committed = self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return []

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def mcq(year, label="1", qid=None) -> TaggedQuestion:
    return TaggedQuestion(
        id=qid or f"q-{year}", paper_slug=f"physics-5054-{year}-may-june-p11", display_label=label,
        ordinal=1, syllabus_code="5054", year=year, season="may_june", component=1, variant=1,
        page_number=2, bbox=(0.0, 0.0, 100.0, 100.0), primary_code="1.1", primary_confidence=0.9,
    )


@pytest.fixture
def mcq_world(monkeypatch, tmp_path):
    import noteacademy_pipeline.load as load
    import noteacademy_pipeline.render as render
    import noteacademy_pipeline.worksheet as worksheet

    conns: list[_Conn] = []
    tagged: list[dict] = []
    state = {"questions": [], "raise": None}

    monkeypatch.chdir(tmp_path)  # crops are written under ./pipeline/work
    monkeypatch.setattr(load, "connect", lambda url: (conns.append(_Conn()) or conns[-1]))
    monkeypatch.setattr(worksheet, "revisable_topics",
                        lambda conn, slug: ("v", [{"code": "1.1", "title": "T",
                                                   "learning_objectives": ["o"]}]))
    monkeypatch.setattr(
        render, "crop", lambda pdf, page, bbox, out, **k: out.write_bytes(b"x") or out
    )
    monkeypatch.setattr(verify, "load_tagged_questions", lambda conn, slug: state["questions"])
    monkeypatch.setattr(verify, "group_duplicates",
                        lambda qs, papers: (qs, {q.id: [q.id] for q in qs}))
    monkeypatch.setattr(verify, "_apply_one", lambda *a, **k: None)

    def fake_tag(path, topics, *, thinking=None):
        tagged.append({"path": path.name, "thinking": thinking})
        if state["raise"]:
            raise state["raise"]
        return TopicAssignment(topic_code="1.1", confidence=0.9, reasoning="r")

    monkeypatch.setattr("noteacademy_pipeline.tagging.tag_from_crop", fake_tag)

    papers = tmp_path / "papers"

    def put_pdf(q):
        path = papers / q.syllabus_code / caie_filename(
            q.syllabus_code, q.year, q.season, "qp", q.component, q.variant)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")

    return dict(state=state, conns=conns, tagged=tagged, papers=papers, put_pdf=put_pdf)


def run_mcq(world, **kw):
    return verify_mcqs_with_model("physics-5054", world["papers"], confidence_floor=0.75, **kw)


def years_tagged(world) -> list[int]:
    return sorted(int(t["path"].split("physics-5054-")[1][:4]) for t in world["tagged"])


def test_only_the_requested_years_are_verified(mcq_world):
    qs = [mcq(y) for y in (2012, 2016, 2019, 2026, 2027)]
    for q in qs:
        mcq_world["put_pdf"](q)
    mcq_world["state"]["questions"] = qs

    run_mcq(mcq_world, dry_run=True, year_from=2016, year_to=2026)
    assert years_tagged(mcq_world) == [2016, 2019, 2026]  # both ends inclusive, the rest untouched


def test_no_year_arguments_verifies_everything_as_before(mcq_world):
    qs = [mcq(y) for y in (2012, 2016, 2027)]
    for q in qs:
        mcq_world["put_pdf"](q)
    mcq_world["state"]["questions"] = qs

    run_mcq(mcq_world, dry_run=True)
    assert years_tagged(mcq_world) == [2012, 2016, 2027]


def test_thinking_reaches_every_call_and_workers_do_not_change_the_outcome(mcq_world):
    qs = [mcq(2016 + i, label=str(i), qid=f"q{i}") for i in range(8)]
    for q in qs:
        mcq_world["put_pdf"](q)
    mcq_world["state"]["questions"] = qs

    run_mcq(mcq_world, dry_run=True, thinking=True, workers=4)
    assert len(mcq_world["tagged"]) == 8
    assert all(t["thinking"] is True for t in mcq_world["tagged"])


def test_a_rejected_key_aborts_the_mcq_run_before_the_write_pass(mcq_world):
    qs = [mcq(2016 + i, label=str(i), qid=f"q{i}") for i in range(5)]
    for q in qs:
        mcq_world["put_pdf"](q)
    mcq_world["state"]["questions"] = qs
    mcq_world["state"]["raise"] = DeepSeekAccountError("out of balance (402)")

    with pytest.raises(DeepSeekAccountError):
        run_mcq(mcq_world, dry_run=False, workers=3)
    assert len(mcq_world["conns"]) == 1  # the read connection only


def test_a_dry_run_still_rolls_back(mcq_world):
    q = mcq(2020)
    mcq_world["put_pdf"](q)
    mcq_world["state"]["questions"] = [q]
    report = run_mcq(mcq_world, dry_run=True, year_from=2016, year_to=2026)
    assert isinstance(report, VerifyReport)
    assert mcq_world["conns"][-1].rolled_back and not mcq_world["conns"][-1].committed
