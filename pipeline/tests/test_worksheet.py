"""What `apply_worksheet` writes, against a database fake that records SQL.

The case that matters here is replacement: a question that already carries a
topic and is given a different one. Unsetting `is_primary` and leaving the old
row behind kept the question listed under a topic no pass had chosen, which is
how 85 retagged chemistry MCQs ended up double-tagged (see docs/roadmap.md).
"""

from __future__ import annotations

from noteacademy_pipeline.worksheet import apply_worksheet

TOPIC_ROWS = [{"code": "1.1", "id": "t-1.1"}, {"code": "2.2", "id": "t-2.2"}]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        self.conn.statements.append((flat, params))
        if "from topics t" in flat:
            self.rows = TOPIC_ROWS
        elif flat.startswith("select id, extraction_status"):
            self.rows = [{"id": params[0], "extraction_status": self.conn.status}]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, status="extracted"):
        self.statements: list[tuple[str, tuple | None]] = []
        self.status = status

    def cursor(self):
        return FakeCursor(self)

    def writes(self, verb):
        return [(s, p) for s, p in self.statements if s.startswith(verb)]


def apply(code="2.2", confidence=0.9, status="extracted"):
    conn = FakeConn(status)
    report = apply_worksheet(
        conn, "chemistry-5070",
        [{"id": "q1", "topic_code": code, "confidence": confidence}],
        confidence_floor=0.75,
    )
    return conn, report


def test_the_superseded_topic_is_deleted_not_left_as_a_secondary():
    conn, report = apply()
    deletes = conn.writes("delete")
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert "question_topics" in sql and "is_primary" in sql
    assert params == ("q1", "t-2.2")          # every primary that is not the new one
    assert report.tagged == 1


def test_nothing_is_demoted_any_more():
    conn, _ = apply()
    assert not [s for s, _ in conn.statements if "set is_primary = false" in s]


def test_the_new_topic_is_written_as_the_primary():
    conn, _ = apply()
    inserts = conn.writes("insert")
    assert len(inserts) == 1
    assert inserts[0][1] == ("q1", "t-2.2", 0.9)


def test_re_applying_the_same_topic_does_not_delete_it():
    # The delete is scoped `topic_id <> the new one`, so an unchanged decision
    # leaves the row for the upsert to update rather than removing and re-adding.
    conn, _ = apply(code="1.1")
    assert conn.writes("delete")[0][1] == ("q1", "t-1.1")


def test_a_code_outside_the_syllabus_writes_nothing_at_all():
    conn, report = apply(code="9.9")
    assert conn.writes("delete") == [] and conn.writes("insert") == []
    assert report.unknown_codes == ["9.9"]


def test_below_the_floor_the_question_is_held_for_review():
    conn, report = apply(confidence=0.5)
    assert report.flagged == 1
    assert any("needs_review" in s for s, _ in conn.writes("update"))


def test_an_approved_question_is_not_dragged_back_into_the_queue():
    # The status update is scoped to unapproved rows; assert the guard is there.
    conn, _ = apply(confidence=0.5, status="approved")
    holds = [s for s, _ in conn.writes("update") if "needs_review" in s]
    assert holds and "extraction_status <> 'approved'" in holds[0]
