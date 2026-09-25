"""First-pass topic tagging for multiple-choice questions, by model, from text.

The step between `mcq-options` (a vision read that puts each question's stem and
options into the database) and `tag-verify-auto` (a second, vision read of the
printed crop). This one reads only the *text* — the stem, the four options and
the keyed answer — and picks a topic from the syllabus's closed list, so the
verifier that follows is working from a different signal and not from the same
words a second time.

Only questions with no topic yet are touched, and only ones whose text has been
read: a question with no stem and no options has nothing to classify, and
guessing from an empty prompt would write a confident-looking tag onto nothing.
Those are counted and reported, not tagged.

As in the verifiers, no database connection is open while the model is being
called. The questions are read in one short pass, every call is made
concurrently, and the results are written in a second short pass.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .config import settings
from .deepseek import DeepSeekAccountError
from .naming import caie_filename
from .parallel import map_concurrently
from .tagging import TopicOption, tag_question
from .worksheet import apply_worksheet, revisable_topics

log = logging.getLogger(__name__)


@dataclass
class UntaggedMcq:
    id: str
    paper_slug: str
    display_label: str
    question_text: str
    options: dict[str, str]
    correct_option: str | None
    # Only loaded for the from-crops path, which needs to re-render the printed
    # question; None everywhere else.
    syllabus_code: str = ""
    year: int = 0
    season: str = ""
    component: int = 0
    variant: int | None = None
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class TagAutoReport:
    candidates: int = 0
    tagged: int = 0
    held_for_review: int = 0
    skipped_no_text: int = 0
    failed_calls: list[str] = field(default_factory=list)
    unknown_codes: list[str] = field(default_factory=list)
    by_topic: dict[str, int] = field(default_factory=dict)
    retagged: int = 0
    changed_topic: int = 0
    backup: Path | None = None
    # from-crops only: no recorded crop, or no source PDF to render it from.
    skipped_no_crop: int = 0
    skipped_no_pdf: int = 0


def load_untagged_mcqs(
    conn: psycopg.Connection,
    subject_slug: str,
    *,
    year_from: int,
    year_to: int,
    paper_slug: str | None = None,
    limit: int | None = None,
    include_tagged: bool = False,
) -> list[UntaggedMcq]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            select q.id, q.display_label, q.question_text, q.correct_option,
                   p.slug as paper_slug, p.component, p.variant,
                   es.year, es.season, s.syllabus_code,
                   qa.page_number, qa.bbox
              from questions q
              join papers p on p.id = q.paper_id
              join subjects s on s.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
              left join question_assets qa
                on qa.question_id = q.id and qa.kind = 'question_crop'
             where s.slug = %s and q.question_type = 'mcq'
               and es.year between %s and %s
               {"" if include_tagged else
                "and not exists (select 1 from question_topics qt where qt.question_id = q.id)"}
               {"and p.slug = %s" if paper_slug else ""}
             order by es.year, p.slug, q.ordinal
             {"limit %s" if limit else ""}
            """,
            tuple(x for x in (subject_slug, year_from, year_to, paper_slug, limit)
                  if x is not None),
        )
        rows = cur.fetchall()
        options: dict[str, dict[str, str]] = {}
        if rows:
            cur.execute(
                "select question_id, option, content from question_options"
                " where question_id = any(%s)",
                ([r["id"] for r in rows],),
            )
            for row in cur.fetchall():
                options.setdefault(row["question_id"], {})[row["option"]] = row["content"] or ""

    return [
        UntaggedMcq(
            id=str(r["id"]), paper_slug=r["paper_slug"], display_label=r["display_label"],
            question_text=(r["question_text"] or "").strip(),
            options=options.get(r["id"], {}), correct_option=r["correct_option"],
            syllabus_code=r.get("syllabus_code", ""), year=r.get("year", 0),
            season=r.get("season", ""), component=r.get("component", 0),
            variant=r.get("variant"), page_number=r.get("page_number"),
            bbox=tuple(r["bbox"]) if r.get("bbox") else None,
        )
        for r in rows
    ]


def _current_tags(conn: psycopg.Connection, question_ids: list[str]) -> dict[str, str]:
    if not question_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select qt.question_id, t.code
              from question_topics qt join topics t on t.id = qt.topic_id
             where qt.is_primary and qt.question_id = any(%s)
            """,
            (question_ids,),
        )
        return {str(r["question_id"]): r["code"] for r in cur.fetchall()}


def _write_backup(
    paper_slug: str | None, questions: list[UntaggedMcq], old_tags: dict[str, str]
) -> Path:
    import csv

    path = settings.work_dir / f"retag-before-{paper_slug}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question_id", "label", "previous_primary_topic"])
        for question in questions:
            writer.writerow([question.id, question.display_label, old_tags.get(question.id, "")])
    return path


def render_question(question: UntaggedMcq) -> str:
    lines = [question.question_text] if question.question_text else []
    lines += [f"{letter}. {text}" for letter, text in sorted(question.options.items())]
    return "\n".join(lines)


def _mark_scheme(question: UntaggedMcq) -> str | None:
    if not question.correct_option:
        return None
    answer = question.options.get(question.correct_option, "")
    return f"Correct answer: {question.correct_option}" + (f". {answer}" if answer else "")


def tag_mcqs_from_crops(
    subject_slug: str,
    papers_dir: Path,
    *,
    year_from: int,
    year_to: int,
    confidence_floor: float,
    dry_run: bool = False,
    workers: int = 1,
    thinking: bool | None = None,
    paper_slug: str | None = None,
    crop_dir: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> TagAutoReport:
    """Tag the multiple-choice questions that have no text to tag from.

    The counterpart to `tag_mcqs_with_model`, for exactly the population that
    one skips: a question whose stem and options are all artwork -- a circuit,
    a graph, four diagrams -- so `mcq-options` found nothing to write and the
    text pass has nothing to read. There are only ever a handful per subject,
    but they are otherwise untaggable and so stay invisible to students for
    good.

    Reads the printed crop instead, with the same vision call
    `tag-verify-auto` uses as its second opinion. Confidence is capped below
    the floor by default: one read of a picture, with no text and no second
    opinion to check it against, is a lead for a person and not a verdict.
    """
    from .load import connect
    from .render import crop as render_crop
    from .tagging import tag_from_crop

    with connect(settings.database_url) as conn:
        _, topic_rows = revisable_topics(conn, subject_slug)
        questions = [
            q for q in load_untagged_mcqs(
                conn, subject_slug, year_from=year_from, year_to=year_to,
                paper_slug=paper_slug,
            )
            if not q.question_text and not q.options
        ]

    topics = [TopicOption(**row) for row in topic_rows]
    report = TagAutoReport(candidates=len(questions))

    crop_dir = crop_dir or Path("pipeline/work") / f"{subject_slug}-textless-crops"
    crop_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1a: render locally and in order -- PyMuPDF is not thread-safe.
    work: list[tuple[UntaggedMcq, Path]] = []
    for question in questions:
        if not question.bbox or not question.page_number:
            report.skipped_no_crop += 1
            continue
        pdf_path = papers_dir / question.syllabus_code / caie_filename(
            question.syllabus_code, question.year, question.season, "qp",
            question.component, question.variant,
        )
        if not pdf_path.is_file():
            report.skipped_no_pdf += 1
            continue
        path = crop_dir / f"{question.paper_slug}-{question.display_label}.png"
        if not path.is_file():
            render_crop(pdf_path, question.page_number, question.bbox, path)
        work.append((question, path))

    # Phase 1b: the calls, with no connection open.
    outcomes = map_concurrently(
        work,
        lambda item: tag_from_crop(item[1], topics, thinking=thinking),
        workers=workers,
        stop_on=(DeepSeekAccountError,),
        on_done=on_progress,
    )

    decisions: list[dict] = []
    for (question, _path), assignment, error in outcomes:
        if error is not None or assignment is None:
            report.failed_calls.append(f"{question.paper_slug} Q{question.display_label}")
            log.warning("tagging %s Q%s from its crop failed: %s",
                        question.paper_slug, question.display_label, error)
            continue
        decisions.append({
            "id": question.id,
            "topic_code": assignment.topic_code,
            # Held below the floor on purpose: see the docstring.
            "confidence": min(assignment.confidence, confidence_floor - 0.01),
        })

    # Phase 2: one short pass of writes.
    with connect(settings.database_url) as conn:
        applied = apply_worksheet(conn, subject_slug, decisions, confidence_floor=confidence_floor)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    report.tagged = applied.tagged
    report.held_for_review = applied.flagged
    report.unknown_codes = applied.unknown_codes
    for decision in decisions:
        code = decision["topic_code"]
        report.by_topic[code] = report.by_topic.get(code, 0) + 1
    return report


def tag_mcqs_with_model(
    subject_slug: str,
    *,
    year_from: int,
    year_to: int,
    confidence_floor: float,
    dry_run: bool = False,
    workers: int = 1,
    paper_slug: str | None = None,
    limit: int | None = None,
    retag: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> TagAutoReport:
    """`retag` replaces the topic of questions that already have one, and puts
    every one of them back into review (status `needs_review`, flag
    `low_tag_confidence`), approved or not: an approved question whose tag was
    wrong is live under the wrong topic, so it comes off until a person
    re-approves it. It needs `paper_slug`, so it can only ever be pointed at
    named papers, and the tags it replaces are written to a CSV first."""
    from .load import connect

    if retag and not paper_slug:
        raise ValueError("retag needs a paper_slug: it replaces tags that a person approved")

    with connect(settings.database_url) as conn:
        _, topic_rows = revisable_topics(conn, subject_slug)
        questions = load_untagged_mcqs(
            conn, subject_slug, year_from=year_from, year_to=year_to,
            paper_slug=paper_slug, limit=limit, include_tagged=retag,
        )
        old_tags = _current_tags(conn, [q.id for q in questions]) if retag else {}

    topics = [TopicOption(**row) for row in topic_rows]
    report = TagAutoReport(candidates=len(questions))

    work = []
    for question in questions:
        if not question.question_text and not question.options:
            report.skipped_no_text += 1
        else:
            work.append(question)

    outcomes = map_concurrently(
        work,
        lambda q: tag_question(render_question(q), topics, mark_scheme=_mark_scheme(q)),
        workers=workers,
        stop_on=(DeepSeekAccountError,),
        on_done=on_progress,
    )

    decisions: list[dict] = []
    for question, tagging, error in outcomes:
        if error is not None or tagging is None:
            report.failed_calls.append(f"{question.paper_slug} Q{question.display_label}")
            log.warning("tagging %s Q%s failed: %s", question.paper_slug,
                        question.display_label, error)
            continue
        decisions.append({
            "id": question.id,
            "topic_code": tagging.primary.topic_code,
            "confidence": tagging.primary.confidence,
        })

    if retag and decisions:
        report.backup = _write_backup(paper_slug, questions, old_tags)

    with connect(settings.database_url) as conn:
        if retag and decisions:
            ids = [d["id"] for d in decisions]
            with conn.cursor() as cur:
                cur.execute("delete from question_topics where question_id = any(%s)", (ids,))
                cur.execute(
                    """
                    update questions
                       set extraction_status = 'needs_review',
                           review_flags = (
                             select array_agg(distinct f)
                               from unnest(review_flags || 'low_tag_confidence'::review_flag) f)
                     where id = any(%s)
                    """,
                    (ids,),
                )
            report.retagged = len(ids)
        applied = apply_worksheet(conn, subject_slug, decisions, confidence_floor=confidence_floor)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    if retag:
        report.changed_topic = sum(
            1 for d in decisions if old_tags.get(d["id"]) != d["topic_code"]
        )
    report.tagged = applied.tagged
    report.held_for_review = applied.flagged
    report.unknown_codes = applied.unknown_codes
    for decision in decisions:
        code = decision["topic_code"]
        report.by_topic[code] = report.by_topic.get(code, 0) + 1
    return report


def tag_structured_with_model(
    subject_slug: str,
    *,
    confidence_floor: float,
    dry_run: bool = False,
    workers: int = 1,
    paper_slug: str | None = None,
    limit: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> TagAutoReport:
    """First-pass tags for structured questions with none, read from text.

    The structured counterpart to `tag_mcqs_with_model`: one tag per top-level
    question, from its own words plus every part beneath it in paper order (the
    same text `tag-export --structured` hands a person). `tag-verify-structured`
    then gives each tag an independent second read from the printed page crops.

    A question with no extracted text at all is skipped and counted, not guessed
    at. Nothing is approved: a tag below the confidence floor is held for review,
    and even above it the question waits for the verifier and the review queue.
    """
    from .load import connect
    from .worksheet import build_structured_worksheet

    with connect(settings.database_url) as conn:
        sheet = build_structured_worksheet(conn, subject_slug, paper_slug=paper_slug, limit=limit)

    topics = [TopicOption(**row) for row in sheet.topics]
    report = TagAutoReport(candidates=len(sheet.questions))

    work = []
    for question in sheet.questions:
        if question.text.strip():
            work.append(question)
        else:
            report.skipped_no_text += 1

    outcomes = map_concurrently(
        work,
        lambda q: tag_question(q.text, topics),
        workers=workers,
        stop_on=(DeepSeekAccountError,),
        on_done=on_progress,
    )

    decisions: list[dict] = []
    for question, tagging, error in outcomes:
        if error is not None or tagging is None:
            report.failed_calls.append(f"{question.paper_slug} Q{question.display_label}")
            log.warning("tagging %s Q%s failed: %s", question.paper_slug,
                        question.display_label, error)
            continue
        decisions.append({
            "id": question.id,
            "topic_code": tagging.primary.topic_code,
            "confidence": tagging.primary.confidence,
        })

    with connect(settings.database_url) as conn:
        applied = apply_worksheet(conn, subject_slug, decisions, confidence_floor=confidence_floor)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    report.tagged = applied.tagged
    report.held_for_review = applied.flagged
    report.unknown_codes = applied.unknown_codes
    for decision in decisions:
        code = decision["topic_code"]
        report.by_topic[code] = report.by_topic.get(code, 0) + 1
    return report
