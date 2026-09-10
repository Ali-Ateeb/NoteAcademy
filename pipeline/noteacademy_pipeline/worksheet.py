"""Tagging as a worksheet: export what needs a topic, apply what was decided.

`tagging.py` calls the API directly, one question at a time. This is the same
job with the classifier taken out of the loop — questions and the closed
syllabus list go out as JSON, decisions come back as JSON — which makes the
classifier a choice rather than a dependency. It can be the API, a person, or a
Claude Code session with no API key at all, and none of them can invent a topic
code because the loader rejects anything outside the list.

The text is pulled from the question paper's own text layer, clipped to the
bounding box segmentation already recorded. That text is not good enough to
*show* a student — reading order in these papers is scrambled and the options
frequently come out backwards — but every word of the question is present, and
for deciding which syllabus outcome a question tests, the words are the whole
signal. Where they are not (a question whose content is four circuit diagrams),
the export says so, and that question wants a human looking at the crop instead
of a classifier reading its caption.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pymupdf

from .naming import caie_filename

log = logging.getLogger(__name__)

# Below this many characters there is not enough left to classify from.
#
# Measured over 5054/11 2019 rather than guessed, and the first guess (100) was
# wrong in an instructive way. Text length runs 69-555 characters, median 277 —
# but the short end is not the unclassifiable end. "Which waves are
# longitudinal?" is 87 characters and unambiguous; the 69-character floor is a
# question whose four options are circuit symbols, and even that one names what
# it is asking about. The topic lives in the *stem*, and the stem is text even
# when the options are artwork.
#
# So this flags only questions with essentially no stem at all. It is advisory:
# the crop is still what a person judges from.
SPARSE_TEXT = 40


@dataclass
class WorksheetQuestion:
    id: str
    paper_slug: str
    display_label: str
    text: str
    correct_option: str | None

    @property
    def is_sparse(self) -> bool:
        return len(self.text) < SPARSE_TEXT


@dataclass
class Worksheet:
    subject_slug: str = ""
    syllabus_label: str = ""
    topics: list[dict] = field(default_factory=list)
    questions: list[WorksheetQuestion] = field(default_factory=list)
    missing_papers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "subject": self.subject_slug,
            "syllabus_version": self.syllabus_label,
            "topics": self.topics,
            "questions": [
                {
                    "id": question.id,
                    "paper": question.paper_slug,
                    "label": question.display_label,
                    "answer": question.correct_option,
                    "text": question.text,
                    **({"sparse": True} if question.is_sparse else {}),
                }
                for question in self.questions
            ],
        }


def revisable_topics(conn: psycopg.Connection, subject_slug: str) -> tuple[str, list[dict]]:
    """The closed list a classifier may choose from.

    Only nodes that carry learning outcomes. A CAIE tree groups '4.2.1'-'4.2.4'
    under '4.2', and '4.2' itself is a heading — offering it as a choice invites
    a tag that is technically true and useless to revise from.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select sv.label, t.code, t.title, t.learning_objectives
              from topics t
              join syllabus_versions sv on sv.id = t.syllabus_version_id
              join subjects s on s.id = sv.subject_id
             where s.slug = %s and sv.is_current
               and coalesce(array_length(t.learning_objectives, 1), 0) > 0
             order by t.sort_order, t.code
            """,
            (subject_slug,),
        )
        rows = cur.fetchall()

    if not rows:
        raise LookupError(
            f"{subject_slug} has no current syllabus with learning outcomes. "
            "Load one with `noteacademy load-syllabus` first."
        )

    label = rows[0]["label"]
    topics = [
        {
            "code": row["code"],
            "title": row["title"],
            "learning_objectives": row["learning_objectives"],
        }
        for row in rows
    ]
    return label, topics


def question_text(pdf: pymupdf.Document, page_number: int, bbox) -> str:
    """The words inside one question's box, whitespace collapsed.

    Reading order is not preserved and is not worth trying to repair here: the
    crop is what a student sees, and this text exists to be classified and
    searched, not read.
    """
    page = pdf[page_number - 1]
    rect = pymupdf.Rect(*bbox) & page.rect
    return " ".join(page.get_text("text", clip=rect).split())


def build_worksheet(
    conn: psycopg.Connection,
    subject_slug: str,
    papers_dir: Path,
    *,
    paper_slug: str | None = None,
    limit: int | None = None,
    include_tagged: bool = False,
) -> Worksheet:
    """Collect the questions that still need a topic, with their text."""
    label, topics = revisable_topics(conn, subject_slug)
    sheet = Worksheet(subject_slug=subject_slug, syllabus_label=label, topics=topics)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            select q.id, q.display_label, q.correct_option, q.ordinal,
                   p.slug as paper_slug, p.component, p.variant,
                   es.year, es.season, sub.syllabus_code,
                   qa.page_number, qa.bbox
              from questions q
              join papers p on p.id = q.paper_id
              join subjects sub on sub.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
              left join question_assets qa
                on qa.question_id = q.id and qa.kind = 'question_crop'
             where sub.slug = %s
               and q.question_type = 'mcq'
               {"" if include_tagged else
                "and not exists (select 1 from question_topics qt"
                " where qt.question_id = q.id)"}
               {"and p.slug = %s" if paper_slug else ""}
             order by es.year desc, p.slug, q.ordinal
             {"limit %s" if limit else ""}
            """,
            tuple(
                x for x in (subject_slug, paper_slug, limit) if x is not None
            ),
        )
        rows = cur.fetchall()

    # One PDF opened per paper, not per question: a paper is forty questions and
    # reopening the file for each is forty times the work for the same bytes.
    by_paper: dict[str, list[dict]] = {}
    for row in rows:
        by_paper.setdefault(row["paper_slug"], []).append(row)

    for slug, questions in by_paper.items():
        first = questions[0]
        name = caie_filename(
            first["syllabus_code"], first["year"], first["season"], "qp",
            first["component"], first["variant"],
        )
        path = papers_dir / first["syllabus_code"] / name
        if not path.is_file():
            sheet.missing_papers.append(str(path))
            continue

        with pymupdf.open(path) as pdf:
            for row in questions:
                text = ""
                if row["bbox"] and row["page_number"]:
                    text = question_text(pdf, row["page_number"], row["bbox"])
                sheet.questions.append(
                    WorksheetQuestion(
                        id=str(row["id"]),
                        paper_slug=slug,
                        display_label=row["display_label"],
                        text=text,
                        correct_option=row["correct_option"],
                    )
                )

    return sheet


def build_structured_worksheet(
    conn: psycopg.Connection,
    subject_slug: str,
    *,
    paper_slug: str | None = None,
    limit: int | None = None,
    include_tagged: bool = False,
) -> Worksheet:
    """The structured counterpart to `build_worksheet`.

    An mcq's text is not in the database at all — it is read fresh from the
    question paper's own PDF here, off the crop's own bbox, because ingestion
    deliberately never extracted it (see ingest.py). A structured question's
    text *is* already in the database: segmentation reads it directly off the
    page as it segments, so there is no PDF to reopen and no missing-paper
    case to report.

    Tagged per top-level question, the same practice unit everywhere else in
    the app treats a structured question as one thing — a topic belongs to
    "9", not separately to "9(a)(ii)" and "9(c)(iii)". The text handed to
    the classifier is the top-level question's own words plus every leaf
    underneath it, in paper order: the top-level text alone is often just a
    figure's caption ("Fig. 9.1 shows a satellite..."), and the actual
    syllabus outcome usually only becomes clear once a part asks something
    concrete of it.
    """
    label, topics = revisable_topics(conn, subject_slug)
    sheet = Worksheet(subject_slug=subject_slug, syllabus_label=label, topics=topics)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            select q.id, q.display_label, q.question_text, p.slug as paper_slug
              from questions q
              join papers p on p.id = q.paper_id
              join subjects sub on sub.id = p.subject_id
             where sub.slug = %s
               and q.question_type = 'structured'
               and q.parent_question_id is null
               {"" if include_tagged else
                "and not exists (select 1 from question_topics qt"
                " where qt.question_id = q.id)"}
               {"and p.slug = %s" if paper_slug else ""}
             order by p.slug, q.ordinal
             {"limit %s" if limit else ""}
            """,
            tuple(x for x in (subject_slug, paper_slug, limit) if x is not None),
        )
        top_level = cur.fetchall()

        for row in top_level:
            cur.execute(
                """
                select question_text from questions
                 where paper_id = (select paper_id from questions where id = %s)
                   and display_label like %s
                   and question_text is not null and question_text <> ''
                 order by ordinal
                """,
                (row["id"], row["display_label"] + "(%"),
            )
            parts_text = [r["question_text"] for r in cur.fetchall()]
            text = " ".join(([row["question_text"]] if row["question_text"] else []) + parts_text)

            sheet.questions.append(
                WorksheetQuestion(
                    id=str(row["id"]),
                    paper_slug=row["paper_slug"],
                    display_label=row["display_label"],
                    text=text,
                    correct_option=None,
                )
            )

    return sheet


@dataclass
class ApplyReport:
    tagged: int = 0
    flagged: int = 0
    unknown_codes: list[str] = field(default_factory=list)
    unknown_questions: list[str] = field(default_factory=list)


def apply_worksheet(
    conn: psycopg.Connection,
    subject_slug: str,
    decisions: list[dict],
    *,
    confidence_floor: float,
) -> ApplyReport:
    """Write decisions back as question_topics rows.

    A code outside the syllabus is dropped rather than written, and the question
    is left untagged: the closed list is the guarantee, and a classifier that
    returns something not on it has told you it was guessing.
    """
    report = ApplyReport()

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
        topic_ids = {row["code"]: row["id"] for row in cur.fetchall()}

    for decision in decisions:
        question_id = decision.get("id")
        code = decision.get("topic_code")
        confidence = float(decision.get("confidence", 0.0))

        if code not in topic_ids:
            report.unknown_codes.append(str(code))
            continue

        with conn.cursor() as cur:
            cur.execute(
                "select id, extraction_status from questions where id = %s",
                (question_id,),
            )
            question = cur.fetchone()
            if question is None:
                report.unknown_questions.append(str(question_id))
                continue

            cur.execute(
                "update question_topics set is_primary = false where question_id = %s",
                (question_id,),
            )
            cur.execute(
                """
                insert into question_topics
                  (question_id, topic_id, confidence, source, is_primary)
                values (%s, %s, %s, 'model', true)
                on conflict (question_id, topic_id) do update set
                  confidence = excluded.confidence,
                  source     = excluded.source,
                  is_primary = excluded.is_primary
                """,
                (question_id, topic_ids[code], confidence),
            )

            # Below the floor the tag exists but the question is held back, which
            # is the entire point of having a floor.
            if confidence < confidence_floor:
                cur.execute(
                    """
                    update questions
                       set extraction_status = 'needs_review',
                           review_flags = (
                             select array_agg(distinct f)
                               from unnest(review_flags || 'low_tag_confidence'::review_flag) f
                           )
                     where id = %s and extraction_status <> 'approved'
                    """,
                    (question_id,),
                )
                report.flagged += 1

        report.tagged += 1

    return report
