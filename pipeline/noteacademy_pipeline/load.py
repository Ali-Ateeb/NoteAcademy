"""Write extraction output into Postgres.

Everything here is idempotent: re-running a paper updates rather than duplicates,
because ingestion runs get interrupted and re-run constantly during a backfill.

Nothing written here is visible to students. Rows land as `extracted` or
`needs_review`, and RLS only exposes `approved`, so promoting a paper is a
deliberate act performed in the review queue.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
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
    # What the sitting contains. Known from the syllabus before a single
    # question is loaded, and the arena needs it in order to decide whether a
    # paper can be sat at all — see db/migrations/0011.
    question_type: str | None = None


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
            insert into papers
              (subject_id, exam_session_id, component, variant, slug, question_type)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (subject_id, exam_session_id, component, variant)
              do update set
                slug = excluded.slug,
                question_type = coalesce(excluded.question_type, papers.question_type)
            returning id
            """,
            (
                subject["id"], session_id, ref.component, ref.variant, paper_slug,
                ref.question_type,
            ),
        )
        return cur.fetchone()["id"]


def record_document(
    conn: psycopg.Connection,
    paper_id: str,
    *,
    doc_type: str,
    storage_key: str,
    page_count: int | None = None,
    byte_size: int | None = None,
    checksum: str | None = None,
    source_url: str | None = None,
) -> str:
    """Register one of a paper's documents.

    The checksum is the point of this row. CAIE occasionally republishes a
    document after we have already extracted questions from it, and without a
    recorded hash that goes unnoticed: the bank keeps serving questions that no
    longer match the paper a student downloads.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into paper_documents
              (paper_id, doc_type, storage_key, page_count, byte_size, checksum, source_url)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (paper_id, doc_type) do update set
              storage_key = excluded.storage_key,
              page_count  = excluded.page_count,
              byte_size   = excluded.byte_size,
              checksum    = excluded.checksum,
              source_url  = coalesce(excluded.source_url, paper_documents.source_url),
              ingested_at = now()
            returning id
            """,
            (paper_id, doc_type, storage_key, page_count, byte_size, checksum, source_url),
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
    review_flags: Sequence[str] = (),
) -> str:
    """Insert or update one question. Keyed on (paper_id, display_label)."""
    # A flag is a reason, so a reason implies the flag: the two cannot disagree.
    flagged = flagged or bool(review_flags)
    status = "needs_review" if flagged else "extracted"

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into questions (
              paper_id, parent_question_id, label, display_label, ordinal,
              question_type, max_marks, question_text, mark_scheme_text,
              examiner_comment, correct_option, extraction_status,
              extraction_confidence, review_flags
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
              review_flags          = excluded.review_flags,
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
                list(review_flags),
            ),
        )
        return cur.fetchone()["id"]


def replace_options(
    conn: psycopg.Connection, question_id: str, options: dict[str, str]
) -> None:
    """Set a multiple-choice question's option text, replacing what is there.

    Replaced rather than merged: a re-extraction that reads three options where
    there were four has found something different on the page, and leaving the
    fourth behind would silently blend two readings of the same question.
    """
    with conn.cursor() as cur:
        cur.execute("delete from question_options where question_id = %s", (question_id,))
        for letter, content in sorted(options.items()):
            cur.execute(
                """
                insert into question_options (question_id, option, content)
                values (%s, %s, %s)
                """,
                (question_id, letter.upper(), content),
            )


def clear_assets(conn: psycopg.Connection, question_id: str, kind: str) -> None:
    """Drop a question's assets of one kind, so re-running replaces them.

    `attach_asset` has no natural key to conflict on — a crop is identified by
    its box, and a re-run that moves the box by a point is the same crop, not a
    new one. Clearing first is what keeps a second ingestion run from leaving
    the same question with two overlapping crops.
    """
    with conn.cursor() as cur:
        cur.execute(
            "delete from question_assets where question_id = %s and kind = %s",
            (question_id, kind),
        )


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
    sort_order: int = 0,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into question_assets
              (question_id, kind, storage_key, page_number, bbox, width_px, height_px, sort_order)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                question_id, kind, storage_key, page_number, list(bbox),
                width_px, height_px, sort_order,
            ),
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


def upsert_syllabus_version(
    conn: psycopg.Connection,
    subject_slug: str,
    *,
    label: str,
    first_exam_year: int,
    last_exam_year: int | None,
    source_url: str | None = None,
    is_current: bool = False,
) -> str:
    """Insert or update a syllabus version, and settle which one is current.

    Exactly one version per subject may be current — a partial unique index says
    so — and the demotion has to happen in the same transaction as the
    promotion, or the insert fails against the version it is replacing.
    """
    with conn.cursor() as cur:
        cur.execute("select id from subjects where slug = %s", (subject_slug,))
        subject = cur.fetchone()
        if subject is None:
            raise LookupError(f"unknown subject slug {subject_slug!r}")

        if is_current:
            cur.execute(
                "update syllabus_versions set is_current = false "
                " where subject_id = %s and label <> %s",
                (subject["id"], label),
            )

        cur.execute(
            """
            insert into syllabus_versions
              (subject_id, label, first_exam_year, last_exam_year, source_url, is_current)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (subject_id, label) do update set
              first_exam_year = excluded.first_exam_year,
              last_exam_year  = excluded.last_exam_year,
              source_url      = coalesce(excluded.source_url,
                                         syllabus_versions.source_url),
              is_current      = excluded.is_current
            returning id
            """,
            (
                subject["id"], label, first_exam_year, last_exam_year,
                source_url, is_current,
            ),
        )
        return cur.fetchone()["id"]


def upsert_topic(
    conn: psycopg.Connection,
    syllabus_version_id: str,
    *,
    code: str,
    title: str,
    slug: str,
    learning_objectives: Sequence[str],
    parent_topic_id: str | None,
    sort_order: int,
) -> str:
    """Insert or update one node of the topic tree, keyed on its CAIE code.

    Keyed on the code rather than the title because the code is what questions
    are tagged with. A retitled topic is the same topic; a renumbered one is a
    different one, and should not silently inherit another topic's questions.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into topics
              (syllabus_version_id, parent_topic_id, code, title, slug,
               learning_objectives, sort_order)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (syllabus_version_id, code) do update set
              parent_topic_id     = excluded.parent_topic_id,
              title               = excluded.title,
              slug                = excluded.slug,
              learning_objectives = excluded.learning_objectives,
              sort_order          = excluded.sort_order
            returning id
            """,
            (
                syllabus_version_id, parent_topic_id, code, title, slug,
                list(learning_objectives), sort_order,
            ),
        )
        return cur.fetchone()["id"]


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
