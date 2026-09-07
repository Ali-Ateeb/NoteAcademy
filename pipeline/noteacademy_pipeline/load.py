"""Write extraction output into Postgres.

Everything here is idempotent: re-running a paper updates rather than duplicates,
because ingestion runs get interrupted and re-run constantly during a backfill.

Nothing written here is visible to students. Rows land as `extracted` or
`needs_review`, and RLS only exposes `approved`, so promoting a paper is a
deliberate act performed in the review queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from .schemas import ExtractedQuestion, TopicTagging
from .tagging import needs_review

log = logging.getLogger(__name__)


@dataclass
class PaperRef:
    subject_slug: str
    year: int
    season: str
    component: int
    variant: int | None


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def resolve_paper_id(conn: psycopg.Connection, ref: PaperRef) -> str:
    """Find the paper row, creating the session and paper if they are new."""
    with conn.cursor() as cur:
        cur.execute("select id from subjects where slug = %s", (ref.subject_slug,))
        subject = cur.fetchone()
        if subject is None:
            raise LookupError(f"unknown subject slug {ref.subject_slug!r}")

        session_slug = f"{ref.year}-{ref.season.replace('_', '-')}"
        cur.execute(
            """
            insert into exam_sessions (year, season, slug)
            values (%s, %s, %s)
            on conflict (year, season) do update set slug = excluded.slug
            returning id
            """,
            (ref.year, ref.season, session_slug),
        )
        session_id = cur.fetchone()["id"]

        variant_part = f"p{ref.component}{ref.variant}" if ref.variant is not None \
            else f"p{ref.component}"
        paper_slug = f"{ref.subject_slug}-{session_slug}-{variant_part}"

        cur.execute(
            """
            insert into papers (subject_id, exam_session_id, component, variant, slug)
            values (%s, %s, %s, %s, %s)
            on conflict (subject_id, exam_session_id, component, variant)
              do update set slug = excluded.slug
            returning id
            """,
            (subject["id"], session_id, ref.component, ref.variant, paper_slug),
        )
        return cur.fetchone()["id"]


def upsert_question(
    conn: psycopg.Connection,
    paper_id: str,
    question: ExtractedQuestion,
    *,
    ordinal: int,
    parent_id: str | None = None,
    correct_option: str | None = None,
    mark_scheme_text: str | None = None,
    examiner_comment: str | None = None,
    confidence: float | None = None,
    flagged: bool = False,
) -> str:
    """Insert or update one question. Keyed on (paper_id, display_label)."""
    status = "needs_review" if flagged else "extracted"

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into questions (
              paper_id, parent_question_id, label, display_label, ordinal,
              question_type, max_marks, question_text, mark_scheme_text,
              examiner_comment, correct_option, extraction_status,
              extraction_confidence
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (paper_id, display_label) do update set
              parent_question_id    = excluded.parent_question_id,
              ordinal               = excluded.ordinal,
              question_type         = excluded.question_type,
              max_marks             = excluded.max_marks,
              question_text         = excluded.question_text,
              mark_scheme_text      = coalesce(excluded.mark_scheme_text,
                                               questions.mark_scheme_text),
              examiner_comment      = coalesce(excluded.examiner_comment,
                                               questions.examiner_comment),
              correct_option        = coalesce(excluded.correct_option,
                                               questions.correct_option),
              extraction_confidence = excluded.extraction_confidence,
              -- A paper already signed off by a human stays signed off; a
              -- re-run must not silently revoke an approval.
              extraction_status     = case
                when questions.extraction_status = 'approved' then 'approved'
                else excluded.extraction_status
              end
            returning id
            """,
            (
                paper_id,
                parent_id,
                question.display_label.split("(")[-1].rstrip(")") or question.display_label,
                question.display_label,
                ordinal,
                question.question_type,
                question.max_marks,
                question.question_text,
                mark_scheme_text,
                examiner_comment,
                correct_option if question.question_type == "mcq" else None,
                status,
                confidence,
            ),
        )
        return cur.fetchone()["id"]


def attach_asset(
    conn: psycopg.Connection,
    question_id: str,
    *,
    kind: str,
    storage_key: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    width_px: int | None = None,
    height_px: int | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into question_assets
              (question_id, kind, storage_key, page_number, bbox, width_px, height_px)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (question_id, kind, storage_key, page_number, list(bbox), width_px, height_px),
        )


def apply_tagging(
    conn: psycopg.Connection,
    question_id: str,
    tagging: TopicTagging,
    topic_ids_by_code: dict[str, str],
) -> None:
    """Write topic assignments, and mark the question for review if unsure."""
    assignments = [(tagging.primary, True)] + [(t, False) for t in tagging.secondary]

    with conn.cursor() as cur:
        cur.execute("delete from question_topics where question_id = %s", (question_id,))
        for assignment, is_primary in assignments:
            topic_id = topic_ids_by_code.get(assignment.topic_code)
            if topic_id is None:
                log.warning("dropping unknown topic code %r", assignment.topic_code)
                continue
            cur.execute(
                """
                insert into question_topics
                  (question_id, topic_id, confidence, source, is_primary)
                values (%s, %s, %s, 'model', %s)
                on conflict (question_id, topic_id) do update set
                  confidence = excluded.confidence,
                  is_primary = excluded.is_primary
                """,
                (question_id, topic_id, assignment.confidence, is_primary),
            )

        if needs_review(tagging):
            cur.execute(
                """
                update questions set extraction_status = 'needs_review'
                 where id = %s and extraction_status = 'extracted'
                """,
                (question_id,),
            )


def load_topic_codes(conn: psycopg.Connection, subject_slug: str) -> dict[str, str]:
    """Topic code -> id for the subject's current syllabus version."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select t.code, t.id
              from topics t
              join syllabus_versions sv on sv.id = t.syllabus_version_id
              join subjects s on s.id = sv.subject_id
             where s.slug = %s and sv.is_current
            """,
            (subject_slug,),
        )
        return {row["code"]: row["id"] for row in cur.fetchall()}
