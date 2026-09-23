"""Automated second-pass tag verification for structured questions.

The structured counterpart to `verify.verify_mcqs_with_model`. A model reads
each question's printed page-crops and chooses its topics, and that read is
compared against the first pass — which worked from extracted *text* — so an
error in one signal is not simply repeated by the other.

Two things make a structured question different from a multiple-choice item,
and both shape this module:

  * It spans one to four page-crops, all of which the model must see, in order.
  * It usually tests several topics, so "primary topic differs" is not the same
    thing as "the two reads disagree". Two readers can legitimately list the
    same two topics in opposite order. The comparison therefore has three
    outcomes rather than two (see `classify_outcome`), and only the last is a
    real conflict.

What this will and will not change is deliberate, and stricter than the
multiple-choice verifier:

  * agreed        -> the primary tag's confidence is raised (`confirm_primary`).
  * reordered     -> nothing is written; both reads found the same topics.
  * disagreed, question NOT yet approved -> as for multiple choice: confidence
                     lowered, the model's topic added as a secondary tag, the
                     question flagged and returned to the review queue.
  * disagreed, question ALREADY approved -> confidence lowered and the finding
                     reported, and *nothing else*. An approved question is live
                     to students; a wrong second opinion (these are read by a
                     model, and a model is wrong some of the time) must not add
                     a topic to it or take it off the site. A person decides,
                     from the triage report this writes.

Nothing touches the database while a model call is in flight — see
`verify_mcqs_with_model` for why (a pooler reclaiming an idle connection killed
a real run). Every crop is rendered and every call made with no connection
open; one short pass of writes follows.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .config import settings
from .deepseek import DeepSeekAccountError
from .naming import caie_filename
from .parallel import map_concurrently
from .schemas import TopicAssignment, TopicTagging
from .tagging import MAX_STRUCTURED_PAGES, TopicOption, tag_structured_from_crops
from .verify import confirm_primary

log = logging.getLogger(__name__)

AGREED = "agreed"
REORDERED = "reordered"
DISAGREED = "disagreed"


@dataclass
class StructuredQuestion:
    id: str
    paper_slug: str
    display_label: str
    ordinal: int
    syllabus_code: str
    year: int
    season: str
    component: int
    variant: int | None
    # One entry per page the question runs across, in reading order.
    crops: list[tuple[int, tuple[float, float, float, float]]]


@dataclass
class Finding:
    """One question's result, kept for the triage report."""

    paper_slug: str
    display_label: str
    status: str
    outcome: str
    file_primary: str
    file_confidence: float
    file_secondary: list[str]
    model_primary: str
    model_confidence: float
    model_secondary: list[str]
    reasoning: str
    pages: int
    changed: bool  # whether this run wrote anything for the question


@dataclass
class StructuredVerifyReport:
    agreed: int = 0
    reordered: int = 0
    disagreed: int = 0
    # Of `disagreed`, how many were already approved — reported, not acted on.
    disagreed_approved: int = 0
    skipped_no_crops: int = 0
    skipped_no_pdf: int = 0
    skipped_too_many_pages: int = 0
    # Deleted, re-tagged to nothing, or otherwise gone between the read and
    # the write; the write pass re-reads current state rather than trusting
    # what was loaded minutes earlier.
    skipped_changed: int = 0
    unknown_codes: list[str] = field(default_factory=list)
    failed_calls: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # Where the model's own answers were saved, before any write was attempted.
    results_path: Path | None = None
    # Replay only: rows another session still holds, and rows that would not write.
    skipped_locked: int = 0
    failed_writes: list[str] = field(default_factory=list)


def _save_results(
    subject_slug: str,
    results: list[tuple[StructuredQuestion, int, TopicTagging]],
    path: Path | None,
) -> Path:
    """Persist the model's answers so a failed write pass costs a retry of the
    writes rather than of the calls. Keyed by question id, which is what the
    write pass re-reads current state by."""
    path = path or (
        settings.work_dir / f"verify-structured-{subject_slug}-results.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for question, pages, tagging in results:
            handle.write(json.dumps({
                "question_id": question.id,
                "paper_slug": question.paper_slug,
                "display_label": question.display_label,
                "pages": pages,
                "primary": tagging.primary.topic_code,
                "confidence": tagging.primary.confidence,
                "reasoning": tagging.primary.reasoning,
                "secondary": [t.topic_code for t in tagging.secondary],
            }, ensure_ascii=False) + "\n")
    return path


def classify_outcome(file_primary: str, file_secondary: list[str], model: TopicTagging) -> str:
    """How two independent reads of one question relate.

    `agreed`: the same primary topic.
    `reordered`: different primaries, but the reads share a topic — the model's
        primary is somewhere on file, or the file's primary is somewhere in the
        model's list. Both saw the same material; they ranked it differently,
        which for a multi-topic question is a judgement call and not an error.
    `disagreed`: nothing in common. This is the only outcome that is evidence
        one of the two reads is wrong.
    """
    model_primary = model.primary.topic_code
    if model_primary == file_primary:
        return AGREED

    on_file = {file_primary, *file_secondary}
    from_model = {model_primary, *(t.topic_code for t in model.secondary)}
    if model_primary in on_file or file_primary in from_model:
        return REORDERED
    return DISAGREED


def load_structured_questions(
    conn: psycopg.Connection,
    subject_slug: str,
    *,
    year_from: int,
    year_to: int,
    paper_slugs: list[str] | None = None,
) -> list[StructuredQuestion]:
    """Every tagged top-level structured question in a subject, within a range
    of sittings, with the page-crops it is printed across.

    Only questions that already carry a primary tag are returned: there is
    nothing to compare an untagged one against.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.id, q.display_label, q.ordinal,
                   p.slug as paper_slug, p.component, p.variant,
                   es.year, es.season, sub.syllabus_code,
                   coalesce(
                     (select jsonb_agg(
                               jsonb_build_object('page', qa.page_number, 'bbox', qa.bbox)
                               order by qa.sort_order, qa.page_number
                             )
                        from question_assets qa
                       where qa.question_id = q.id and qa.kind = 'question_crop'),
                     '[]'::jsonb
                   ) as crops
              from questions q
              join papers p on p.id = q.paper_id
              join subjects sub on sub.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
              join question_topics qt on qt.question_id = q.id and qt.is_primary
             where sub.slug = %s
               and p.question_type = 'structured'
               and q.parent_question_id is null
               and es.year between %s and %s
               and (%s::text[] is null or p.slug = any(%s::text[]))
             order by es.year desc, p.slug, q.ordinal
            """,
            (subject_slug, year_from, year_to, paper_slugs, paper_slugs),
        )
        rows = cur.fetchall()

    return [
        StructuredQuestion(
            id=str(row["id"]),
            paper_slug=row["paper_slug"],
            display_label=row["display_label"],
            ordinal=row["ordinal"],
            syllabus_code=row["syllabus_code"],
            year=row["year"],
            season=row["season"],
            component=row["component"],
            variant=row["variant"],
            crops=[(int(c["page"]), tuple(c["bbox"])) for c in row["crops"]],
        )
        for row in rows
    ]


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def verify_structured_with_model(
    subject_slug: str,
    papers_dir: Path,
    *,
    year_from: int,
    year_to: int,
    confidence_floor: float,
    dry_run: bool = False,
    thinking: bool | None = None,
    workers: int = 1,
    paper_slugs: list[str] | None = None,
    crop_dir: Path | None = None,
    results_path: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> StructuredVerifyReport:
    """Verify a subject's structured question tags against a model's read of
    the printed pages. See the module docstring for what is and is not
    written; `dry_run` rolls every write back and still fills the report."""
    from .load import connect
    from .render import crop as render_crop
    from .worksheet import revisable_topics

    with connect(settings.database_url) as conn:
        _, topic_rows = revisable_topics(conn, subject_slug)
        questions = load_structured_questions(
            conn, subject_slug, year_from=year_from, year_to=year_to, paper_slugs=paper_slugs
        )

    topics = [TopicOption(**row) for row in topic_rows]
    report = StructuredVerifyReport()

    crop_dir = crop_dir or Path("pipeline/work") / f"{subject_slug}-verify-structured-crops"
    crop_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1a: render every page-crop, here and in order — local and quick, and
    # PyMuPDF is not something to share between threads.
    work: list[tuple[StructuredQuestion, list[Path]]] = []
    for question in questions:
        if not question.crops:
            report.skipped_no_crops += 1
            continue
        if len(question.crops) > MAX_STRUCTURED_PAGES:
            log.warning(
                "%s %s has %d page crops — more than %d, skipping (check its segmentation)",
                question.paper_slug, question.display_label,
                len(question.crops), MAX_STRUCTURED_PAGES,
            )
            report.skipped_too_many_pages += 1
            continue

        pdf_path = papers_dir / question.syllabus_code / caie_filename(
            question.syllabus_code, question.year, question.season, "qp",
            question.component, question.variant,
        )
        if not pdf_path.is_file():
            report.skipped_no_pdf += 1
            continue

        paths: list[Path] = []
        for index, (page_number, bbox) in enumerate(question.crops, start=1):
            stem = f"{_safe(question.paper_slug)}-{_safe(question.display_label)}"
            path = crop_dir / f"{stem}-{index}.png"
            if not path.is_file():
                render_crop(pdf_path, page_number, bbox, path)
            paths.append(path)
        work.append((question, paths))

    # Phase 1b: every model call, concurrently, with no database connection open.
    # A wrong key or an empty balance ends the whole run (`stop_on`) while
    # nothing has been written.
    outcomes = map_concurrently(
        work,
        lambda item: tag_structured_from_crops(item[1], topics, thinking=thinking),
        workers=workers,
        stop_on=(DeepSeekAccountError,),
        on_done=on_progress,
    )

    results: list[tuple[StructuredQuestion, int, TopicTagging]] = []
    for (question, paths), tagging, error in outcomes:
        if error is not None:
            log.warning(
                "verifying %s %s failed: %s", question.paper_slug, question.display_label, error
            )
            report.failed_calls.append(f"{question.paper_slug} {question.display_label}")
            continue
        if tagging.primary.confidence == 0.0:
            # `_validated` zeroes the confidence of a code outside the syllabus.
            report.unknown_codes.append(tagging.primary.topic_code)
            continue
        results.append((question, len(paths), tagging))

    # Phase 1c: the model's answers, on disk, before anything is written.
    #
    # Everything above is the expensive part — hundreds of vision calls — and
    # the write pass below is the fragile part: one connection, hundreds of
    # statements, over a link that does drop (a real run lost ~900 calls'
    # worth of work to `server closed the connection unexpectedly` partway
    # through). Saving here means a failed or rolled-back write costs a retry
    # of the writes, not of the spend.
    report.results_path = _save_results(subject_slug, results, results_path)
    log.info("model results saved to %s", report.results_path)

    # Phase 2: every write, in one short connection.
    with connect(settings.database_url) as conn:
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

        for question, pages, tagging in results:
            apply_structured_one(
                conn, question, pages, tagging, topic_ids, confidence_floor, report
            )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    return report


def resume_structured_from_results(
    subject_slug: str,
    results_path: Path,
    *,
    confidence_floor: float,
    dry_run: bool = False,
    lock_timeout_ms: int = 4000,
) -> StructuredVerifyReport:
    """Replay saved model answers (`_save_results`) through the ordinary write
    pass, without calling the model again.

    Written for the failure this file already records twice: the calls succeed
    and the write pass dies. It differs from that pass in two ways, both
    because a replay runs against a database that may still be holding the
    wreckage of the run before it:

      * one transaction per question, not one for the batch, so a single
        unavailable row cannot roll back six hundred good writes;
      * a short `lock_timeout`, so a row still locked by an abandoned session
        is skipped and reported rather than stalling the whole replay until
        the statement timeout kills it.

    Both make this safe to run repeatedly: `apply_structured_one` re-reads each
    question's current state, so a question already written is simply seen as
    agreeing with itself.
    """
    from .load import connect

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = StructuredVerifyReport(results_path=results_path)
    report.findings = []

    with connect(settings.database_url) as conn:
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

        for row in rows:
            if row["primary"] not in topic_ids:
                report.unknown_codes.append(row["primary"])
                continue
            question = StructuredQuestion(
                id=row["question_id"], paper_slug=row["paper_slug"],
                display_label=row["display_label"], ordinal=0, syllabus_code="",
                year=0, season="", component=0, variant=None, crops=[],
            )
            tagging = TopicTagging(
                primary=TopicAssignment(
                    topic_code=row["primary"], confidence=row["confidence"],
                    reasoning=row.get("reasoning", ""),
                ),
                secondary=[
                    TopicAssignment(topic_code=code, confidence=0.6, reasoning="")
                    for code in row.get("secondary", [])
                ],
            )
            try:
                with conn.cursor() as cur:
                    # SET takes no bind parameters, hence the interpolation;
                    # the value is an int this function's own caller chose.
                    cur.execute(f"set local lock_timeout = {int(lock_timeout_ms)}")
                apply_structured_one(
                    conn, question, row.get("pages", 1), tagging,
                    topic_ids, confidence_floor, report,
                )
                if dry_run:
                    conn.rollback()
                else:
                    conn.commit()
            except psycopg.errors.LockNotAvailable:
                conn.rollback()
                report.skipped_locked += 1
                log.warning(
                    "%s %s is locked by another session; skipped",
                    row["paper_slug"], row["display_label"],
                )
            except psycopg.Error as error:
                conn.rollback()
                report.failed_writes.append(f"{row['paper_slug']} {row['display_label']}")
                log.warning("%s %s failed to write: %s",
                            row["paper_slug"], row["display_label"], error)

    return report


def apply_structured_one(
    conn: psycopg.Connection,
    question: StructuredQuestion,
    pages: int,
    model: TopicTagging,
    topic_ids: dict[str, str],
    floor: float,
    report: StructuredVerifyReport,
) -> None:
    """Compare one model result with the question's *current* tags and act on
    it as the module docstring describes.

    Current, not as loaded: the read happened minutes ago and a reviewer may
    have re-tagged or approved the question since, so the comparison is made
    against what is in the database now.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select t.code, qt.confidence, q.extraction_status
              from question_topics qt
              join topics t on t.id = qt.topic_id
              join questions q on q.id = qt.question_id
             where qt.question_id = %s and qt.is_primary
            """,
            (question.id,),
        )
        row = cur.fetchone()
        if row is None:
            report.skipped_changed += 1
            return

        file_primary = row["code"]
        file_confidence = float(row["confidence"])
        approved = row["extraction_status"] == "approved"

        cur.execute(
            """
            select t.code
              from question_topics qt
              join topics t on t.id = qt.topic_id
             where qt.question_id = %s and not qt.is_primary
             order by t.code
            """,
            (question.id,),
        )
        file_secondary = [r["code"] for r in cur.fetchall()]

        outcome = classify_outcome(file_primary, file_secondary, model)
        changed = False

        if outcome == AGREED:
            confirm_primary(cur, question.id, file_confidence, approved=approved)
            report.agreed += 1
            changed = True

        elif outcome == REORDERED:
            report.reordered += 1

        else:
            # Below the floor whatever the first pass believed: a second read
            # landing somewhere with nothing in common is new information a
            # single confident guess did not have.
            cur.execute(
                "update question_topics set confidence = %s"
                " where question_id = %s and is_primary",
                (min(file_confidence, floor - 0.01), question.id),
            )
            changed = True
            if approved:
                # Live to students: report it, do not act on it (see module docstring).
                report.disagreed_approved += 1
            else:
                cur.execute(
                    """
                    insert into question_topics
                      (question_id, topic_id, confidence, source, is_primary)
                    values (%s, %s, %s, 'verifier', false)
                    on conflict (question_id, topic_id) do update set
                      confidence = excluded.confidence,
                      source     = excluded.source
                    """,
                    (question.id, topic_ids[model.primary.topic_code], 0.6),
                )
                cur.execute(
                    """
                    update questions
                       set review_flags = (
                             select array_agg(distinct f)
                               from unnest(
                                 review_flags || 'low_tag_confidence'::review_flag
                               ) f
                           ),
                           extraction_status = 'needs_review'
                     where id = %s
                    """,
                    (question.id,),
                )
            report.disagreed += 1

    report.findings.append(
        Finding(
            paper_slug=question.paper_slug,
            display_label=question.display_label,
            status="approved" if approved else str(row["extraction_status"]),
            outcome=outcome,
            file_primary=file_primary,
            file_confidence=file_confidence,
            file_secondary=file_secondary,
            model_primary=model.primary.topic_code,
            model_confidence=model.primary.confidence,
            model_secondary=[t.topic_code for t in model.secondary],
            reasoning=model.primary.reasoning,
            pages=pages,
            changed=changed,
        )
    )


_CSV_FIELDS = [
    "outcome", "paper", "question", "status", "pages",
    "file_primary", "file_confidence", "file_secondary",
    "model_primary", "model_confidence", "model_secondary", "reasoning", "wrote",
]

# Disagreements first, then reorderings, then agreements: the file is a triage
# list, and the rows a person must look at should be the first ones they see.
_OUTCOME_ORDER = {DISAGREED: 0, REORDERED: 1, AGREED: 2}


def write_findings_csv(findings: list[Finding], path: Path) -> Path:
    """The triage report: every question's outcome, disagreements first, with
    the model's reasoning beside the tags on file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        findings,
        key=lambda f: (_OUTCOME_ORDER[f.outcome], f.paper_slug, f.display_label.zfill(4)),
    )
    with path.open("w", newline="", encoding="utf-8-sig") as handle:  # BOM: opens cleanly in Excel
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for f in ordered:
            writer.writerow(
                {
                    "outcome": f.outcome,
                    "paper": f.paper_slug,
                    "question": f.display_label,
                    "status": f.status,
                    "pages": f.pages,
                    "file_primary": f.file_primary,
                    "file_confidence": f"{f.file_confidence:.2f}",
                    "file_secondary": " ".join(f.file_secondary),
                    "model_primary": f.model_primary,
                    "model_confidence": f"{f.model_confidence:.2f}",
                    "model_secondary": " ".join(f.model_secondary),
                    "reasoning": f.reasoning,
                    "wrote": "yes" if f.changed else "no",
                }
            )
    return path
