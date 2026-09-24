"""Structured tag verification without a database, a PDF, or a network call.

The database is a scripted fake that records every statement, which is what
lets the safety rules be asserted directly: an *approved* question must never
receive an inserted tag or a status change, whatever the model says.
"""

from __future__ import annotations

import csv
import json

import pytest

from noteacademy_pipeline import verify_structured
from noteacademy_pipeline.config import Settings
from noteacademy_pipeline.deepseek import DeepSeekAccountError
from noteacademy_pipeline.naming import caie_filename
from noteacademy_pipeline.schemas import TopicAssignment, TopicTagging
from noteacademy_pipeline.verify_structured import (
    AGREED,
    DISAGREED,
    REORDERED,
    Finding,
    StructuredQuestion,
    StructuredVerifyReport,
    apply_structured_one,
    classify_outcome,
    verify_structured_with_model,
    write_findings_csv,
)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def tagging(primary: str, *secondary: str, confidence: float = 0.9) -> TopicTagging:
    return TopicTagging(
        primary=TopicAssignment(topic_code=primary, confidence=confidence, reasoning="because"),
        secondary=[
            TopicAssignment(topic_code=code, confidence=0.6, reasoning="also") for code in secondary
        ],
    )


class FakeCursor:
    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.conn.statements.append((flat, params))
        self.rows = self.conn.respond(flat, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConn:
    closed = False
    broken = False

    def __init__(self, respond):
        self.respond = respond
        self.statements: list[tuple[str, tuple | None]] = []
        self.committed = False
        self.rolled_back = False

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def writes(self) -> list[str]:
        return [s for s, _ in self.statements if not s.lower().startswith("select")]


def responder(*, primary="1.1", confidence=0.85, status="approved", secondary=(), topics=None):
    def respond(sql, params):
        if sql.startswith("select t.code, qt.confidence, q.extraction_status"):
            if primary is None:
                return []
            return [{"code": primary, "confidence": confidence, "extraction_status": status}]
        if sql.startswith("select t.code from question_topics"):
            return [{"code": c} for c in secondary]
        if sql.startswith("select t.code, t.id"):
            return [{"code": c, "id": f"id-{c}"} for c in (topics or ["1.1", "1.2", "2.1", "3.1"])]
        return []

    return respond


def question(**overrides) -> StructuredQuestion:
    base = dict(
        id="q-1", paper_slug="physics-5054-2020-may-june-p21", display_label="3", ordinal=3,
        syllabus_code="5054", year=2020, season="may_june", component=2, variant=1,
        crops=[(4, (10.0, 20.0, 500.0, 700.0))],
    )
    base.update(overrides)
    return StructuredQuestion(**base)


IDS = {"1.1": "id-1.1", "1.2": "id-1.2", "2.1": "id-2.1", "3.1": "id-3.1"}


def apply(conn, q, model, report=None, floor=0.75, pages=1):
    report = report or StructuredVerifyReport()
    apply_structured_one(conn, q, pages, model, IDS, floor, report)
    return report


# --------------------------------------------------------------------------
# classify_outcome
# --------------------------------------------------------------------------


def test_same_primary_is_agreement():
    assert classify_outcome("1.1", [], tagging("1.1")) == AGREED
    assert classify_outcome("1.1", ["2.1"], tagging("1.1", "3.1")) == AGREED


def test_models_primary_being_a_secondary_on_file_is_a_reordering():
    assert classify_outcome("1.1", ["2.1"], tagging("2.1")) == REORDERED


def test_files_primary_appearing_in_the_models_list_is_a_reordering():
    assert classify_outcome("1.1", [], tagging("2.1", "1.1")) == REORDERED


def test_the_same_topics_in_the_opposite_order_is_a_reordering():
    assert classify_outcome("1.1", ["2.1"], tagging("2.1", "1.1")) == REORDERED


def test_nothing_in_common_is_a_disagreement():
    assert classify_outcome("1.1", ["2.1"], tagging("3.1", "1.2")) == DISAGREED


def test_a_shared_secondary_alone_is_not_enough():
    # Both mention 2.1 as a secondary, but neither primary is in the other's list.
    assert classify_outcome("1.1", ["2.1"], tagging("3.1", "2.1")) == DISAGREED


# --------------------------------------------------------------------------
# what apply_structured_one writes
# --------------------------------------------------------------------------


def test_agreement_raises_confidence_and_touches_nothing_else_on_an_approved_question():
    conn = FakeConn(responder(primary="1.1", confidence=0.85, status="approved"))
    report = apply(conn, question(), tagging("1.1"))

    assert report.agreed == 1
    writes = conn.writes()
    assert len(writes) == 1 and "update question_topics set confidence" in writes[0]
    assert conn.statements[-1][1] == (0.9, "q-1")  # max(0.85, 0.9)
    assert not any("extraction_status" in w for w in writes)


def test_agreement_on_a_question_awaiting_review_clears_the_stale_reason():
    conn = FakeConn(responder(primary="1.1", confidence=0.6, status="needs_review"))
    apply(conn, question(), tagging("1.1"))

    writes = " | ".join(conn.writes())
    assert "array_remove" in writes and "'extracted'" in writes


def test_a_more_confident_first_pass_is_not_lowered_by_agreement():
    conn = FakeConn(responder(primary="1.1", confidence=0.97, status="approved"))
    apply(conn, question(), tagging("1.1"))
    assert conn.statements[-1][1] == (0.97, "q-1")


def test_a_reordering_writes_nothing_at_all():
    conn = FakeConn(responder(primary="1.1", secondary=("2.1",), status="approved"))
    report = apply(conn, question(), tagging("2.1"))

    assert report.reordered == 1
    assert conn.writes() == []
    assert report.findings[0].changed is False


def test_a_disagreement_on_an_APPROVED_question_only_lowers_its_confidence():
    conn = FakeConn(responder(primary="1.1", confidence=0.85, status="approved"))
    report = apply(conn, question(), tagging("3.1"))

    assert (report.disagreed, report.disagreed_approved) == (1, 1)
    writes = conn.writes()
    assert len(writes) == 1
    assert "update question_topics set confidence" in writes[0]
    assert conn.statements[-1][1] == (0.74, "q-1")  # min(0.85, floor - 0.01)
    # The rules that keep a live question live and correct:
    assert not any("insert into question_topics" in w for w in writes)
    assert not any("needs_review" in w for w in writes)
    assert not any("review_flags" in w for w in writes)


def test_a_disagreement_on_an_unapproved_question_returns_it_to_the_queue():
    conn = FakeConn(responder(primary="1.1", confidence=0.85, status="extracted"))
    report = apply(conn, question(), tagging("3.1"))

    assert (report.disagreed, report.disagreed_approved) == (1, 0)
    writes = " | ".join(conn.writes())
    assert "insert into question_topics" in writes
    assert "needs_review" in writes and "low_tag_confidence" in writes
    insert_sql, insert_params = next(
        (s, p) for s, p in conn.statements if s.startswith("insert into question_topics")
    )
    assert insert_params == ("q-1", "id-3.1", 0.6)
    # source = 'verifier', not 'model': the review route's approve path
    # deletes exactly this row by that column, and must never catch a
    # genuine editorial secondary topic (source 'model') in the same sweep.
    assert "'verifier'" in insert_sql
    assert "'model'" not in insert_sql


def test_a_first_pass_already_below_the_floor_is_not_raised_by_a_disagreement():
    conn = FakeConn(responder(primary="1.1", confidence=0.5, status="approved"))
    apply(conn, question(), tagging("3.1"))
    assert conn.statements[-1][1] == (0.5, "q-1")  # min(0.5, 0.74)


def test_it_compares_against_current_tags_not_the_ones_loaded_earlier():
    # The question was re-tagged to 3.1 while the run was in flight: the model's
    # 3.1 now *agrees*, where against the tag it was loaded with it would not.
    conn = FakeConn(responder(primary="3.1", confidence=0.9, status="approved"))
    report = apply(conn, question(), tagging("3.1"))
    assert report.agreed == 1 and report.disagreed == 0


def test_a_question_that_lost_its_tag_meanwhile_is_skipped_and_nothing_is_written():
    conn = FakeConn(responder(primary=None))
    report = apply(conn, question(), tagging("1.1"))
    assert report.skipped_changed == 1
    assert conn.writes() == [] and report.findings == []


def test_every_result_is_recorded_as_a_finding_with_both_reads():
    conn = FakeConn(
        responder(primary="1.1", secondary=("1.2",), confidence=0.85, status="approved")
    )
    report = apply(conn, question(), tagging("3.1", "2.1"), pages=2)

    (finding,) = report.findings
    assert (finding.outcome, finding.file_primary, finding.file_secondary) == (
        DISAGREED, "1.1", ["1.2"],
    )
    assert (finding.model_primary, finding.model_secondary) == ("3.1", ["2.1"])
    assert (finding.pages, finding.status, finding.changed) == (2, "approved", True)


# --------------------------------------------------------------------------
# the orchestration
# --------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Everything verify_structured_with_model reaches for, faked."""
    import noteacademy_pipeline.load as load
    import noteacademy_pipeline.render as render
    import noteacademy_pipeline.worksheet as worksheet

    conns: list[FakeConn] = []
    state = {"questions": [], "tag": lambda paths, topics, thinking=None: tagging("1.1")}
    calls: list[dict] = []

    def connect(_url):
        conn = FakeConn(responder(primary="1.1", confidence=0.85, status="approved"))
        conns.append(conn)
        return conn

    def fake_tag(paths, topics, *, thinking=None):
        calls.append({"paths": list(paths), "thinking": thinking})
        return state["tag"](paths, topics, thinking)

    def fake_crop(pdf, page, bbox, out, **_):
        out.write_bytes(b"png")
        return out

    monkeypatch.setattr(load, "connect", connect)
    monkeypatch.setattr(render, "crop", fake_crop)
    monkeypatch.setattr(
        worksheet, "revisable_topics",
        lambda conn, slug: ("v", [{"code": "1.1", "title": "T", "learning_objectives": ["o"]}]),
    )
    monkeypatch.setattr(verify_structured, "load_structured_questions",
                        lambda conn, slug, **kw: state["questions"])
    monkeypatch.setattr(verify_structured, "tag_structured_from_crops", fake_tag)

    papers = tmp_path / "papers"

    def with_pdf(q: StructuredQuestion) -> StructuredQuestion:
        path = papers / q.syllabus_code / caie_filename(
            q.syllabus_code, q.year, q.season, "qp", q.component, q.variant
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")
        return q

    return dict(state=state, conns=conns, calls=calls, papers=papers, with_pdf=with_pdf,
                crop_dir=tmp_path / "crops")


def run(world, **kw):
    kw.setdefault("year_from", 2016)
    kw.setdefault("year_to", 2026)
    kw.setdefault("confidence_floor", 0.75)
    return verify_structured_with_model(
        "physics-5054", world["papers"], crop_dir=world["crop_dir"], **kw
    )


def test_a_dry_run_rolls_back_and_a_real_run_commits(world):
    world["state"]["questions"] = [world["with_pdf"](question())]

    run(world, dry_run=True)
    assert world["conns"][-1].rolled_back and not world["conns"][-1].committed

    run(world, dry_run=False)
    assert world["conns"][-1].committed and not world["conns"][-1].rolled_back


def test_every_page_of_a_multi_page_question_reaches_the_model_in_order(world):
    q = question(crops=[(4, (0, 0, 1, 1)), (5, (0, 0, 1, 1)), (6, (0, 0, 1, 1))])
    world["state"]["questions"] = [world["with_pdf"](q)]
    run(world, dry_run=True)

    (call,) = world["calls"]
    assert [p.name.rsplit("-", 1)[1] for p in call["paths"]] == ["1.png", "2.png", "3.png"]


def test_thinking_is_passed_through_to_the_model_call(world):
    world["state"]["questions"] = [world["with_pdf"](question())]
    run(world, dry_run=True, thinking=True)
    assert world["calls"][0]["thinking"] is True


def test_questions_that_cannot_be_verified_are_counted_not_lost(world):
    with_pdf = world["with_pdf"]
    world["state"]["questions"] = [
        with_pdf(question(id="ok")),
        with_pdf(question(id="nocrops", crops=[])),
        with_pdf(question(id="many", crops=[(i, (0, 0, 1, 1)) for i in range(1, 8)])),
        question(id="nopdf", syllabus_code="9999"),
    ]
    report = run(world, dry_run=True)

    skipped = (report.skipped_no_crops, report.skipped_too_many_pages, report.skipped_no_pdf)
    assert skipped == (1, 1, 1)
    assert report.agreed == 1
    assert len(world["calls"]) == 1


def test_one_failed_call_is_recorded_and_the_rest_still_verified(world):
    with_pdf = world["with_pdf"]
    world["state"]["questions"] = [
        with_pdf(question(id="a", display_label="1")),
        with_pdf(question(id="b", display_label="2")),
        with_pdf(question(id="c", display_label="3")),
    ]

    def flaky(paths, topics, thinking=None):
        if paths[0].name.startswith(world["state"]["questions"][1].paper_slug + "-2"):
            raise RuntimeError("bad reply")
        return tagging("1.1")

    world["state"]["tag"] = flaky
    report = run(world, dry_run=True)

    assert report.failed_calls == ["physics-5054-2020-may-june-p21 2"]
    assert report.agreed == 2


def test_a_code_outside_the_syllabus_is_reported_and_never_written(world):
    world["state"]["questions"] = [world["with_pdf"](question())]
    world["state"]["tag"] = lambda paths, topics, thinking=None: tagging("9.9", confidence=0.0)
    report = run(world, dry_run=False)

    assert report.unknown_codes == ["9.9"]
    assert report.agreed == report.disagreed == 0
    assert world["conns"][-1].writes() == []


def test_a_rejected_key_stops_the_run_before_anything_is_written(world):
    with_pdf = world["with_pdf"]
    world["state"]["questions"] = [
        with_pdf(question(id=str(i), display_label=str(i))) for i in range(5)
    ]

    def refuse(paths, topics, thinking=None):
        raise DeepSeekAccountError("DeepSeek rejected the API key (401)")

    world["state"]["tag"] = refuse
    with pytest.raises(DeepSeekAccountError):
        run(world, dry_run=False, workers=3)

    # Only the read connection was ever opened; the write pass never began.
    assert len(world["conns"]) == 1


def test_concurrent_and_sequential_runs_agree(world):
    with_pdf = world["with_pdf"]
    world["state"]["questions"] = [
        with_pdf(question(id=str(i), display_label=str(i))) for i in range(12)
    ]
    a = run(world, dry_run=True, workers=1)
    b = run(world, dry_run=True, workers=6)
    summary = lambda r: (r.agreed, r.disagreed, len(r.findings))  # noqa: E731
    assert summary(a) == summary(b) == (12, 0, 12)


# --------------------------------------------------------------------------
# the triage report
# --------------------------------------------------------------------------


def finding(outcome, paper="p", label="1", **kw) -> Finding:
    base = dict(
        paper_slug=paper, display_label=label, status="approved", outcome=outcome,
        file_primary="1.1", file_confidence=0.85, file_secondary=["2.1"],
        model_primary="3.1", model_confidence=0.9, model_secondary=["1.2"],
        reasoning="because, of a comma", pages=2, changed=True,
    )
    base.update(kw)
    return Finding(**base)


def test_the_triage_csv_lists_disagreements_first_then_reorderings_then_agreements(tmp_path):
    findings = [
        finding(AGREED, label="1"), finding(REORDERED, label="2"), finding(DISAGREED, label="10"),
        finding(DISAGREED, label="2"), finding(AGREED, label="3"),
    ]
    path = write_findings_csv(findings, tmp_path / "out" / "triage.csv")

    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["outcome"], r["question"]) for r in rows] == [
        (DISAGREED, "2"), (DISAGREED, "10"), (REORDERED, "2"), (AGREED, "1"), (AGREED, "3"),
    ]


def test_the_triage_csv_round_trips_the_awkward_fields(tmp_path):
    path = write_findings_csv([finding(DISAGREED)], tmp_path / "t.csv")
    with path.open(encoding="utf-8-sig") as handle:
        (row,) = list(csv.DictReader(handle))

    assert row["reasoning"] == "because, of a comma"
    assert row["file_secondary"] == "2.1" and row["model_secondary"] == "1.2"
    assert row["file_confidence"] == "0.85" and row["wrote"] == "yes"


def test_the_csv_opens_cleanly_in_excel(tmp_path):
    path = write_findings_csv([finding(AGREED)], tmp_path / "t.csv")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 BOM


# --------------------------------------------------------------------------
# configuration this feature depends on
# --------------------------------------------------------------------------


def test_the_default_year_scope_is_2016_to_2026():
    assert (Settings().scope_year_from, Settings().scope_year_to) == (2016, 2026)


def test_the_year_scope_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("NOTEACADEMY_YEAR_FROM", "2019")
    monkeypatch.setenv("NOTEACADEMY_YEAR_TO", "2021")
    s = Settings.from_env()
    assert (s.scope_year_from, s.scope_year_to) == (2019, 2021)


@pytest.mark.parametrize("value", ["twenty", "1850", "3000"])
def test_a_nonsense_year_is_rejected_loudly(monkeypatch, value):
    monkeypatch.setenv("NOTEACADEMY_YEAR_FROM", value)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_a_backwards_range_is_rejected(monkeypatch):
    monkeypatch.setenv("NOTEACADEMY_YEAR_FROM", "2024")
    monkeypatch.setenv("NOTEACADEMY_YEAR_TO", "2020")
    with pytest.raises(ValueError, match="backwards"):
        Settings.from_env()



# --------------------------------------------------------------------------
# the model's answers survive a failed write pass
# --------------------------------------------------------------------------


def test_the_model_results_are_on_disk_before_any_write_is_attempted(
    monkeypatch, tmp_path, world
):
    """A real run lost ~900 vision calls when the database connection dropped
    partway through the write pass. The answers are saved first now, so a retry
    costs the writes and not the spend."""
    world["state"]["questions"] = [world["with_pdf"](question())]
    out = tmp_path / "results.jsonl"

    def explode(*a, **k):
        raise RuntimeError("server closed the connection unexpectedly")

    monkeypatch.setattr(verify_structured, "apply_structured_one", explode)

    with pytest.raises(RuntimeError):
        run(world, results_path=out)

    assert out.is_file(), "results must be saved before the write pass runs"
    saved = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(saved) == 1
    assert saved[0]["primary"] == "1.1"
    assert saved[0]["question_id"] and saved[0]["paper_slug"]


# --------------------------------------------------------------------------
# the write pass and the replay: surviving a dropped connection
# --------------------------------------------------------------------------
#
# The replay used to catch a lost connection like any other error, roll back on
# the dead link, and then fail every remaining row against it. These drive both
# write passes through `commit_each` with a scripted `apply_structured_one`.


def _replay_file(tmp_path, names):
    path = tmp_path / "results.jsonl"
    path.write_text("\n".join(
        json.dumps({
            "question_id": n, "paper_slug": f"paper-{n}", "display_label": "1", "pages": 1,
            "primary": "1.1", "confidence": 0.9, "reasoning": "r", "secondary": [],
        }) for n in names
    ) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def link(monkeypatch):
    """Fake connections, and an `apply_structured_one` that fails on cue.

    `script` maps the Nth call (1-based, across reconnects) to "drop" -- the
    OperationalError a severed socket really raises, with the connection's own
    `closed`/`broken` flags left False -- or "timeout".
    """
    import psycopg

    import noteacademy_pipeline.load as load

    state = {"script": {}, "calls": 0}
    conns: list[FakeConn] = []
    applied: list[str] = []

    def connect(_url):
        conns.append(FakeConn(responder(topics=["1.1", "2.1"])))
        return conns[-1]

    def fake_apply(conn, question, pages, model, topic_ids, floor, report):
        state["calls"] += 1
        action = state["script"].get(state["calls"])
        # Counted before anything can go wrong, as the real applier does: it has
        # tallied a question by the time `commit()` -- which can also die -- runs.
        report.agreed += 1
        report.findings.append(question.id)
        if action == "drop":
            raise psycopg.OperationalError("SSL SYSCALL error: Cannot send after socket shutdown")
        if action == "timeout":
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        applied.append(question.id)

    monkeypatch.setattr(load, "connect", connect)
    monkeypatch.setattr(verify_structured, "apply_structured_one", fake_apply)
    monkeypatch.setattr("noteacademy_pipeline.verify_write.time.sleep", lambda s: None)
    return {"state": state, "conns": conns, "applied": applied}


def replay(tmp_path, names, **kw):
    return verify_structured.resume_structured_from_results(
        "physics-5054", _replay_file(tmp_path, names), confidence_floor=0.75, **kw
    )


def test_each_replayed_question_commits_on_its_own(link, tmp_path):
    report = replay(tmp_path, ["a", "b", "c"])

    assert report.agreed == 3
    assert link["applied"] == ["a", "b", "c"]


def test_a_replay_reconnects_after_a_dropped_link_and_finishes(link, tmp_path):
    link["state"]["script"] = {2: "drop"}
    report = replay(tmp_path, ["a", "b", "c"])

    assert report.reconnects == 1
    assert report.failed_writes == []
    assert link["applied"] == ["a", "b", "c"]  # b was retried on the new connection
    assert len(link["conns"]) == 2


def test_a_retried_question_is_counted_and_reported_once(link, tmp_path):
    link["state"]["script"] = {2: "drop"}
    report = replay(tmp_path, ["a", "b", "c"])

    assert report.agreed == 3
    assert report.findings == ["a", "b", "c"]  # the triage CSV must not repeat a row


def test_a_replay_against_a_database_that_stays_down_lists_what_it_did_not_write(
    link, tmp_path, monkeypatch
):
    link["state"]["script"] = {n: "drop" for n in range(2, 40)}
    report = replay(tmp_path, ["a", "b", "c", "d"])

    assert report.agreed == 1  # "a", committed before the link failed
    assert report.failed_writes == [
        "paper-b 1", "paper-c 1", "paper-d 1",
    ]  # and it ended rather than failing each against a dead connection or raising


def test_a_statement_timeout_costs_one_question_not_the_replay(link, tmp_path):
    link["state"]["script"] = {2: "timeout"}
    report = replay(tmp_path, ["a", "b", "c"])

    assert report.failed_writes == ["paper-b 1"]
    assert report.agreed == 2
    assert report.reconnects == 0


def test_a_replayed_code_outside_the_syllabus_is_reported_not_applied(link, tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({
        "question_id": "x", "paper_slug": "p", "display_label": "1", "pages": 1,
        "primary": "9.9", "confidence": 0.9, "secondary": [],
    }) + "\n", encoding="utf-8")
    report = verify_structured.resume_structured_from_results(
        "physics-5054", path, confidence_floor=0.75
    )

    assert report.unknown_codes == ["9.9"]
    assert link["applied"] == []


def test_a_dry_replay_rolls_every_question_back(link, tmp_path):
    report = replay(tmp_path, ["a", "b"], dry_run=True)

    assert report.agreed == 2
    (conn,) = link["conns"]
    assert conn.rolled_back and not conn.committed


def test_the_main_write_pass_reconnects_too(link, world):
    # Not only the replay: the run's own write pass shares the same code, so a
    # drop during a fresh run no longer costs the whole batch either.
    with_pdf = world["with_pdf"]
    world["state"]["questions"] = [
        with_pdf(question(id="a", display_label="1")),
        with_pdf(question(id="b", display_label="2")),
        with_pdf(question(id="c", display_label="3")),
    ]
    link["state"]["script"] = {2: "drop"}

    report = run(world, dry_run=False)

    assert report.agreed == 3
    assert report.reconnects == 1
    assert report.failed_writes == []
