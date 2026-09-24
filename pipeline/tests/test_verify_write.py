"""The MCQ verifier's write pass: saved answers, per-group commits, reconnecting.

The failure these exist for is a real one: the chemistry run's calls all
succeeded, then `server closed the connection unexpectedly` hit mid-write, the
single transaction rolled back, and the answers -- held only in memory -- went
with it. Every test here fakes the database link; none calls a model.
"""

from __future__ import annotations

import csv

import psycopg
import pytest

from noteacademy_pipeline import verify, verify_write
from noteacademy_pipeline.verify import VerifyReport
from noteacademy_pipeline.verify_write import (
    resume_mcqs_from_results,
    save_mcq_results,
    write_approved_disagreements_csv,
    write_mcq_decisions,
)

TOPIC_ROWS = [{"code": "1.1", "id": "t-1"}, {"code": "2.1", "id": "t-2"}]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append(" ".join(sql.split()).lower())

    def fetchall(self):
        return self.conn.rows


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.broken = False
        self.commits = 0
        self.rollbacks = 0
        self.statements: list[str] = []

    def cursor(self):
        if self.closed:
            raise psycopg.OperationalError("the connection is closed")
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(name, code="1.1", group=None):
    return {
        "question_id": name, "paper_slug": f"paper-{name}", "display_label": "1",
        "topic_code": code, "confidence": 0.9, "group": group or [name],
    }


@pytest.fixture
def world(monkeypatch):
    """A fake database whose `_apply_one` misbehaves on the calls a test names.

    `script` maps the Nth call to `_apply_one` (1-based, across reconnects) to
    what goes wrong: "drop" (the link dies), "timeout" (the server cancels the
    statement, link intact) or "lock".
    """
    import noteacademy_pipeline.load as load

    state = {"script": {}, "calls": 0, "connects": 0, "connect_fails": set()}
    conns: list[FakeConn] = []
    applied: list[tuple[FakeConn, str]] = []

    def fake_connect(url):
        state["connects"] += 1
        if state["connects"] in state["connect_fails"]:
            raise psycopg.OperationalError("could not connect")
        conns.append(FakeConn(TOPIC_ROWS))
        return conns[-1]

    def fake_apply(conn, question_id, code, topic_ids, floor, report, *, leave_approved=False):
        state["calls"] += 1
        action = state["script"].get(state["calls"])
        if action == "drop":
            conn.closed = conn.broken = True
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        if action == "drop_silently":
            # What a severed socket does to real psycopg: an OperationalError,
            # but `closed` and `broken` both stay False.
            raise psycopg.OperationalError("SSL SYSCALL error: Cannot send after socket shutdown")
        if action == "timeout":
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        if action == "lock":
            raise psycopg.errors.LockNotAvailable("could not obtain lock on row")
        applied.append((conn, question_id))
        report.agreed += 1

    monkeypatch.setattr(load, "connect", fake_connect)
    monkeypatch.setattr(verify_write, "_apply_one", fake_apply)
    return {"state": state, "conns": conns, "applied": applied, "slept": []}


def run(world, answers, **kwargs):
    report = VerifyReport()
    write_mcq_decisions(
        "physics-5054", answers, 0.75, report,
        sleep=world["slept"].append, **kwargs,
    )
    return report


# --------------------------------------------------------------------------
# commits
# --------------------------------------------------------------------------


def test_each_group_is_its_own_transaction(world):
    report = run(world, [answer("a"), answer("b"), answer("c")])

    assert report.agreed == 3
    (conn,) = world["conns"]
    assert conn.commits == 3  # not one commit for the batch


def test_a_group_applies_to_every_duplicate_in_one_transaction(world):
    report = run(world, [answer("a", group=["a", "a-dup1", "a-dup2"]), answer("b")])

    assert report.agreed == 4
    (conn,) = world["conns"]
    assert conn.commits == 2
    assert [q for _, q in world["applied"]] == ["a", "a-dup1", "a-dup2", "b"]


def test_a_dry_run_writes_nothing(world):
    report = run(world, [answer("a"), answer("b")], dry_run=True)

    assert report.agreed == 2  # still counted, so the summary means something
    (conn,) = world["conns"]
    assert conn.commits == 0
    assert conn.rollbacks >= 2


def test_a_code_outside_the_syllabus_is_reported_not_written(world):
    report = run(world, [answer("a", code="9.9"), answer("b")])

    assert report.unknown_codes == ["9.9"]
    assert [q for _, q in world["applied"]] == ["b"]


# --------------------------------------------------------------------------
# a dropped connection
# --------------------------------------------------------------------------


def test_a_dropped_connection_is_reopened_and_the_same_group_retried(world):
    # Call 2 is the second duplicate of the first group: the link dies with the
    # group half-applied and nothing committed.
    world["state"]["script"] = {2: "drop"}
    report = run(world, [answer("a", group=["a", "a-dup"]), answer("b")])

    first, second = world["conns"]
    assert report.reconnects == 1
    assert first.commits == 0  # the half-applied group never committed
    assert second.commits == 2  # ...it was redone whole, then the next group
    assert report.failed_writes == []


def test_a_dead_link_is_recognised_even_when_the_connection_flags_say_healthy(world):
    # Found against the real database: with a socket severed under a live
    # connection psycopg leaves conn.closed and conn.broken False. Judged by
    # those flags this read as "the server refused one statement" -- so it
    # rolled back on a dead link and lost every remaining group.
    world["state"]["script"] = {2: "drop_silently"}
    report = run(world, [answer("a"), answer("b"), answer("c")])

    assert report.reconnects == 1
    assert report.failed_writes == []
    assert report.agreed == 3


def test_a_retried_group_is_never_counted_twice(world):
    world["state"]["script"] = {2: "drop"}
    report = run(world, [answer("a", group=["a", "a-dup"]), answer("b")])

    # Three questions in all. The first attempt at group one had already
    # counted one of them before it died.
    assert report.agreed == 3


def test_it_backs_off_between_reconnects(world):
    world["state"]["script"] = {1: "drop", 2: "drop"}
    run(world, [answer("a")])

    assert world["slept"] == [2, 4]


def test_a_reconnect_that_itself_fails_counts_as_another_attempt(world):
    world["state"]["script"] = {1: "drop"}
    world["state"]["connect_fails"] = {2}  # the first connect works; the reconnect does not

    report = run(world, [answer("a")])

    assert report.reconnects == 2  # one for the drop, one for the failed reopen
    assert report.agreed == 1
    assert report.failed_writes == []


def test_a_database_that_stays_down_ends_the_pass_and_lists_what_was_not_written(world):
    world["state"]["script"] = {
        1: None, 2: None,  # group "a" lands
        3: "drop", 4: "drop", 5: "drop", 6: "drop",
    }
    report = run(world, [answer("a", group=["a", "a2"]), answer("b"), answer("c")],
                 max_reconnects=2)

    assert report.agreed == 2  # group "a", committed before the link failed
    assert report.reconnects == 2
    assert report.failed_writes == ["paper-b 1", "paper-c 1"]  # nothing raised


# --------------------------------------------------------------------------
# a failure that leaves the link alone
# --------------------------------------------------------------------------


def test_a_statement_timeout_costs_that_group_only(world):
    world["state"]["script"] = {2: "timeout"}
    report = run(world, [answer("a"), answer("b"), answer("c")])

    assert report.failed_writes == ["paper-b 1"]
    assert report.agreed == 2
    assert report.reconnects == 0
    assert len(world["conns"]) == 1  # the link was fine; nothing reopened


def test_a_row_held_by_another_session_is_skipped_not_waited_for(world):
    world["state"]["script"] = {1: "lock"}
    report = run(world, [answer("a"), answer("b")])

    assert report.skipped_locked == 1
    assert report.agreed == 1


def test_every_group_sets_a_short_lock_timeout(world):
    run(world, [answer("a"), answer("b")], lock_timeout_ms=1234)

    (conn,) = world["conns"]
    assert conn.statements.count("set local lock_timeout = 1234") == 2


# --------------------------------------------------------------------------
# saved answers and replay
# --------------------------------------------------------------------------


def test_saved_answers_round_trip_through_a_replay_without_the_model(world, tmp_path):
    answers = [answer("a", group=["a", "a-dup"]), answer("b", code="2.1")]
    path = save_mcq_results("physics-5054", answers, tmp_path / "results.jsonl")

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2

    report = resume_mcqs_from_results("physics-5054", path, confidence_floor=0.75)
    assert report.agreed == 3
    assert report.results_path == path
    assert [q for _, q in world["applied"]] == ["a", "a-dup", "b"]


def test_a_replay_survives_a_dropped_connection_too(world, tmp_path):
    # The structured verifier's replay fails every remaining row against a dead
    # connection. This one reconnects.
    path = save_mcq_results("physics-5054", [answer("a"), answer("b"), answer("c")],
                            tmp_path / "r.jsonl")
    world["state"]["script"] = {2: "drop"}

    report = resume_mcqs_from_results("physics-5054", path, confidence_floor=0.75)

    assert report.agreed == 3
    assert report.reconnects == 1
    assert report.failed_writes == []


# --------------------------------------------------------------------------
# approved questions the model disagreed with
# --------------------------------------------------------------------------


class ScriptedCursor:
    def __init__(self, row):
        self.row, self.sql = row, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()).lower())

    def fetchone(self):
        return self.row


class ScriptedConn:
    def __init__(self, row):
        self.cur = ScriptedCursor(row)

    def cursor(self):
        return self.cur


def test_an_approved_disagreement_is_recorded_by_id_and_still_writes_nothing():
    conn = ScriptedConn({"code": "1.1", "confidence": 0.85, "extraction_status": "approved"})
    report = VerifyReport()

    verify._apply_one(conn, "q-77", "2.1", {"2.1": "t-2"}, 0.75, report, leave_approved=True)

    assert report.disagreed_approved == 1
    assert report.approved_disagreements == [("q-77", "1.1", "2.1")]
    assert len(conn.cur.sql) == 1  # the read; no update, no insert


def test_the_disagreements_are_written_out_by_paper_and_label(monkeypatch, tmp_path):
    import noteacademy_pipeline.load as load

    rows = [
        {"id": "q-2", "paper_slug": "chemistry-5070-2019-may-june-p12", "display_label": "31"},
        {"id": "q-1", "paper_slug": "chemistry-5070-2018-may-june-p11", "display_label": "7"},
    ]
    monkeypatch.setattr(load, "connect", lambda url: FakeConn(rows))
    report = VerifyReport()
    report.approved_disagreements = [("q-2", "3.1", "3.2"), ("q-1", "1.1", "2.1")]

    path = write_approved_disagreements_csv(report, tmp_path / "out.csv")

    with path.open(encoding="utf-8", newline="") as handle:
        lines = list(csv.DictReader(handle))
    assert [r["paper_slug"] for r in lines] == [
        "chemistry-5070-2018-may-june-p11", "chemistry-5070-2019-may-june-p12",
    ]  # sorted by paper, so they can be worked through in order
    assert lines[0]["display_label"] == "7"
    assert (lines[0]["tag_on_file"], lines[0]["model_tag"]) == ("1.1", "2.1")


def test_no_disagreements_means_no_file(tmp_path):
    assert write_approved_disagreements_csv(VerifyReport(), tmp_path / "none.csv") is None
    assert not (tmp_path / "none.csv").exists()
