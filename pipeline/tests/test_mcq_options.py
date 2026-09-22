"""The option backfill, and the one thing about it that is easy to regress.

It calls a vision model per page. It used to do that with the caller's
transaction open, which left a session idle in transaction for as long as each
call took — holding its locks, and, on a database with
`idle_in_transaction_session_timeout = 0`, indefinitely if the run died. The
tests that matter here assert the shape that prevents it: connections are
opened for the read, closed, and opened again for the write, with none held
across a call.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from noteacademy_pipeline import mcq_options
from noteacademy_pipeline.mcq_options import _Plan, backfill_paper_options


@dataclass
class FakeOption:
    letter: str
    text: str


@dataclass
class FakeQuestion:
    display_label: str
    question_type: str = "mcq"
    question_text: str | None = "What is a mole?"
    options: list[FakeOption] | None = None


@dataclass
class FakeExtracted:
    questions: list[FakeQuestion]
    is_content_page: bool = True


@dataclass
class FakePage:
    page_number: int


class FakeConn:
    """Records whether it was open while the model was called."""

    def __init__(self, world):
        self.world = world
        self.closed = False
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def cursor(self):
        return self

    def __iter__(self):
        return iter(())

    def execute(self, sql, params=None):
        self.world["sql"].append(" ".join(sql.split()).lower())

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def commit(self):
        self.committed = True


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = {
        "sql": [],
        "conns": [],
        "open_during_calls": [],
        "plan": _Plan(paper_slug="physics-5054-2020-may-june-p11"),
        "pages": [FakePage(1)],
        "extracted": FakeExtracted([
            FakeQuestion("1", options=[FakeOption("A", "one"), FakeOption("B", "two")]),
        ]),
        "written": [],
        "texts": [],
    }

    def fake_connect(url):
        conn = FakeConn(state)
        state["conns"].append(conn)
        return conn

    def fake_extract(page):
        # The whole point: nothing may be open here.
        state["open_during_calls"].append(
            [c for c in state["conns"] if not c.closed]
        )
        return state["extracted"]

    monkeypatch.setattr("noteacademy_pipeline.load.connect", fake_connect)
    monkeypatch.setattr("noteacademy_pipeline.extract.extract_page", fake_extract)
    monkeypatch.setattr("noteacademy_pipeline.extract.cross_check", lambda page, ex: False)
    monkeypatch.setattr(mcq_options, "_plan_paper", lambda conn, pdf: state["plan"])
    monkeypatch.setattr(mcq_options, "render_pdf", lambda pdf, work: state["pages"])
    monkeypatch.setattr(
        "noteacademy_pipeline.load.replace_options",
        lambda conn, qid, options: state["written"].append((qid, options)),
    )
    monkeypatch.setattr(
        mcq_options, "settings",
        type("S", (), {"work_dir": tmp_path, "database_url": "postgres://x"})(),
    )
    return state


def run(world, **kw):
    return backfill_paper_options(world.get("pdf", "5054_s20_qp_11.pdf"), **kw)


def test_no_connection_is_open_while_the_model_is_called(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = ["q1"]
    run(world)
    assert world["open_during_calls"], "the model was never called"
    for still_open in world["open_during_calls"]:
        assert still_open == [], "a connection was held across a vision call"


def test_it_connects_once_to_read_and_once_to_write(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = ["q1"]
    run(world)
    assert len(world["conns"]) == 2
    assert world["conns"][0].closed and not world["conns"][0].committed  # the read
    assert world["conns"][1].committed                                   # the write


def test_a_dry_run_never_opens_the_write_connection(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = ["q1"]
    report = run(world, dry_run=True)
    assert len(world["conns"]) == 1
    assert world["written"] == []
    assert report.matched == 1 and report.written == 0


def test_a_paper_with_nothing_left_to_do_calls_no_model_and_writes_nothing(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = []
    report = run(world)
    assert world["open_during_calls"] == []
    assert len(world["conns"]) == 1
    assert report.written == 0


def test_duplicates_are_copied_without_a_model_call(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = []
    world["plan"].copyable = {"q1": {"A": "one", "B": "two"}}
    report = run(world)
    assert world["open_during_calls"] == []      # no page was ever sent
    assert report.copied_from_duplicate == 1
    assert world["written"] == [("q1", {"A": "one", "B": "two"})]


def test_a_label_the_paper_does_not_have_is_reported_not_written(world):
    world["plan"].existing = {}                  # the extracted "1" matches nothing
    world["plan"].remaining = ["q1"]
    report = run(world)
    assert report.unmatched_labels == ["1"]
    assert world["written"] == []


def test_a_question_with_fewer_than_two_options_is_skipped(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = ["q1"]
    world["extracted"] = FakeExtracted([FakeQuestion("1", options=[FakeOption("A", "one")])])
    report = run(world)
    assert report.matched == 0 and world["written"] == []


def test_a_figure_option_is_flagged_but_still_written(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = ["q1"]
    world["extracted"] = FakeExtracted([
        FakeQuestion("1", options=[FakeOption("A", "[Figure: a circuit]"), FakeOption("B", "two")]),
    ])
    report = run(world)
    assert report.figure_options == ["1"]
    assert report.written == 1


def test_pages_with_nothing_left_on_them_are_never_sent(world):
    world["plan"].existing = {"1": "q1"}
    world["plan"].remaining = ["q1"]
    world["plan"].needed_pages = {2}             # page 1 is already resolved
    world["pages"] = [FakePage(1), FakePage(2)]
    report = run(world)
    assert report.pages_skipped == 1
    assert len(world["open_during_calls"]) == 1  # only page 2 reached the model
