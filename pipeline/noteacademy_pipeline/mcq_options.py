"""Backfill MCQ question and option text via the vision pass.

`ingest.py` deliberately loads an MCQ paper from geometry alone — a crop, a
number, an answer — because CAIE's reading order is scrambled and some options
are diagrams, not text (see its own docstring). This module is the vision pass
that docstring points to: it re-reads an already-loaded question paper with
`extract_page` (the same call structured papers use, whose schema already
carries an `options` field for exactly this case) and writes what it finds
onto the existing rows with `replace_options`.

Additive, not a re-ingestion: the questions this runs against are already
loaded and, in most cases, already approved and being sat. A page the model
misreads leaves the row exactly as it was — a crop with no option text, the
status this whole corpus has always shipped in — so there is no failure mode
here worse than doing nothing.

Duplicate-aware, which is the actual cost lever: `dedupe`/`verify.py` already
establish that CAIE's paper variants (p11/p12/p13 of one sitting) share most
of their multiple-choice items verbatim, and mark the repeats via
`canonical_question_id`. A question whose canonical copy already has its
options backfilled gets them copied from that row for free — no vision call —
and a page whose every question is resolved that way is never sent to the
model at all. Run this p11 first, then p12/p13 of the same sitting, and the
later runs cost roughly (1 - duplicate fraction) of a from-scratch backfill.
Already-populated questions are skipped the same way, which is what makes
re-running this on a paper idempotent-and-free rather than idempotent-and-
re-billed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .config import settings
from .naming import parse_paper_filename
from .render import render_pdf

log = logging.getLogger(__name__)

# A model describing a figure per its own instructions ("[Figure: ...]"),
# rather than transcribing one, has told us the option is not text at all.
_FIGURE_MARKER = "[figure"


@dataclass
class OptionsReport:
    paper_slug: str = ""
    matched: int = 0
    written: int = 0
    copied_from_duplicate: int = 0
    pages_skipped: int = 0
    unmatched_labels: list[str] = field(default_factory=list)
    figure_options: list[str] = field(default_factory=list)
    flagged_pages: list[int] = field(default_factory=list)


def _already_backfilled(conn: psycopg.Connection, question_ids: list[str]) -> set[str]:
    if not question_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "select distinct question_id from question_options where question_id = any(%s)",
            (question_ids,),
        )
        return {row["question_id"] for row in cur.fetchall()}


def _duplicate_options(
    conn: psycopg.Connection, question_ids: list[str]
) -> dict[str, dict[str, str]]:
    """Option text already sitting on another paper's copy of the same question.

    A verbatim duplicate (see `verify.group_duplicates`, which is what marks
    `canonical_question_id` in the first place) is the same question printed
    twice — its options are the same words, not a second, independent
    reading of them — so copying is not a weaker substitute for vision here,
    it is the correct answer at zero cost.
    """
    if not question_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.id as dest_id, o.option, o.content
              from questions q
              join question_options o on o.question_id = q.canonical_question_id
             where q.id = any(%s) and q.canonical_question_id is not null
            """,
            (question_ids,),
        )
        by_dest: dict[str, dict[str, str]] = {}
        for row in cur.fetchall():
            by_dest.setdefault(row["dest_id"], {})[row["option"]] = row["content"]
    # A partial set (some options copyable, others not, e.g. the canonical's
    # own backfill only found 3 of 4) is not trustworthy: fall through to
    # vision for that question rather than writing an incomplete answer.
    return {dest: opts for dest, opts in by_dest.items() if len(opts) >= 2}


def _canonical_question_text(conn: psycopg.Connection, question_ids: list[str]) -> dict[str, str]:
    if not question_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.id as dest_id, c.question_text
              from questions q
              join questions c on c.id = q.canonical_question_id
             where q.id = any(%s) and q.question_text is null
               and c.question_text is not null and c.question_text <> ''
            """,
            (question_ids,),
        )
        return {row["dest_id"]: row["question_text"] for row in cur.fetchall()}


def _pages_needing_vision(
    conn: psycopg.Connection, question_ids: list[str]
) -> set[int]:
    """Which pages still have at least one unresolved question on them.

    Only `question_crop` assets are addressed: every mcq question carries one
    regardless of whether `load-mcq --crops` was also asked to write a local
    PNG, so this is a reliable page number for every question, not just the
    ones with a file on disk.
    """
    if not question_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """
            select page_number from question_assets
             where question_id = any(%s) and kind = 'question_crop'
               and page_number is not null
            """,
            (question_ids,),
        )
        pages = {row["page_number"] for row in cur.fetchall()}
    if len(pages) < len(question_ids):
        # A question with no recorded page cannot be excluded from any page's
        # scope — better to vision-extract everything than to silently skip
        # one that turned out to need it.
        return set()
    return pages


@dataclass
class _Plan:
    """What the read pass worked out, carried across the model calls."""

    paper_slug: str
    existing: dict[str, str] = field(default_factory=dict)     # display label -> question id
    resolved: set[str] = field(default_factory=set)            # needs no model call
    copyable: dict[str, dict[str, str]] = field(default_factory=dict)
    copyable_text: dict[str, str] = field(default_factory=dict)
    needed_pages: set[int] = field(default_factory=set)
    remaining: list[str] = field(default_factory=list)


def _plan_paper(conn: psycopg.Connection, qp_pdf: Path) -> _Plan:
    """Everything the write pass will need, read in one pass up front."""
    from .ingest import resolve_subject_slug

    ref = parse_paper_filename(qp_pdf.stem)
    subject_slug = resolve_subject_slug(conn, ref.syllabus_code)

    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, p.slug from papers p
              join subjects s on s.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
             where s.slug = %s and es.year = %s and es.season = %s
               and p.component = %s
               and p.variant is not distinct from %s
            """,
            (subject_slug, ref.year, ref.season, ref.component, ref.variant),
        )
        paper = cur.fetchone()
        if paper is None:
            raise LookupError(f"{qp_pdf.name} has no matching paper row — load it first")
        paper_id, paper_slug = paper["id"], paper["slug"]

        cur.execute(
            "select id, display_label from questions"
            " where paper_id = %s and question_type = 'mcq'",
            (paper_id,),
        )
        existing = {row["display_label"]: row["id"] for row in cur.fetchall()}

    plan = _Plan(paper_slug=paper_slug, existing=existing)
    all_ids = list(existing.values())

    plan.resolved = _already_backfilled(conn, all_ids)
    remaining = [qid for qid in all_ids if qid not in plan.resolved]

    plan.copyable = _duplicate_options(conn, remaining)
    plan.copyable_text = _canonical_question_text(conn, remaining)
    plan.resolved |= plan.copyable.keys()

    plan.remaining = [qid for qid in all_ids if qid not in plan.resolved]
    plan.needed_pages = _pages_needing_vision(conn, plan.remaining)
    return plan


def backfill_paper_options(
    qp_pdf: Path,
    *,
    dry_run: bool = False,
) -> OptionsReport:
    """Extract and write option text for one already-loaded MCQ paper.

    Matches the vision pass's questions back to existing rows by paper slug +
    display label — the same identity the initial geometric load keyed on —
    rather than inserting anything new.

    Three ways a question can end this without a model call: it already has
    options from an earlier run (idempotent re-runs are free), it is a marked
    duplicate of a question that does (copied, not re-read), or every question
    on its page falls into one of those two buckets (the page itself is never
    sent to the model).

    Connects twice, and holds no connection while a model call is in flight —
    the same three-phase shape as the verifiers, for the same two reasons. A
    transaction left open across a vision call sat *idle in transaction* for as
    long as the call took, holding its locks; with
    `idle_in_transaction_session_timeout` at 0, one such session that outlived
    its run blocked a later one indefinitely. And a connection merely held open
    across minutes of model calls is what a pooler reclaims as idle, which has
    already killed a real run (see `verify.verify_mcqs_with_model`).
    """
    from .extract import cross_check, extract_page
    from .load import connect, replace_options

    # Phase 1: read what is needed, then let the connection go.
    with connect(settings.database_url) as conn:
        plan = _plan_paper(conn, qp_pdf)

    report = OptionsReport(paper_slug=plan.paper_slug)
    report.copied_from_duplicate = len(plan.copyable)

    # Phase 2: render and call the model, with no connection open at all.
    extracted_options: list[tuple[str, str, dict[str, str], str | None]] = []
    if plan.remaining:
        work = settings.work_dir / f"{plan.paper_slug}-options"
        pages = render_pdf(qp_pdf, work)
        if plan.needed_pages:
            report.pages_skipped = sum(
                1 for p in pages if p.page_number not in plan.needed_pages
            )
            pages = [p for p in pages if p.page_number in plan.needed_pages]

        for page in pages:
            extracted = extract_page(page)
            if not extracted.is_content_page:
                continue
            if cross_check(page, extracted):
                report.flagged_pages.append(page.page_number)

            for question in extracted.questions:
                if question.question_type != "mcq" or not question.options:
                    continue
                options = {
                    e.letter: e.text for e in question.options if e.text and e.text.strip()
                }
                if len(options) < 2:
                    continue

                question_id = plan.existing.get(question.display_label)
                if question_id is None:
                    report.unmatched_labels.append(question.display_label)
                    continue
                if question_id in plan.resolved:
                    continue

                report.matched += 1
                if any(v.strip().lower().startswith(_FIGURE_MARKER) for v in options.values()):
                    report.figure_options.append(question.display_label)
                text = (question.question_text or "").strip() or None
                extracted_options.append(
                    (question_id, question.display_label, options, text)
                )

    if dry_run or not (plan.copyable or extracted_options):
        return report

    # Phase 3: one short pass of writes.
    with connect(settings.database_url) as conn:
        for question_id, options in plan.copyable.items():
            replace_options(conn, question_id, options)
            _set_question_text(conn, question_id, plan.copyable_text.get(question_id))
        for question_id, _label, options, text in extracted_options:
            replace_options(conn, question_id, options)
            report.written += 1
            _set_question_text(conn, question_id, text)
        conn.commit()

    return report


def _set_question_text(conn: psycopg.Connection, question_id: str, text: str | None) -> None:
    """Fill a question's text only if it has none: a later, worse read must not
    overwrite what an earlier pass already established."""
    if not text:
        return
    with conn.cursor() as cur:
        cur.execute(
            "update questions set question_text = %s"
            " where id = %s and question_text is null",
            (text, question_id),
        )
