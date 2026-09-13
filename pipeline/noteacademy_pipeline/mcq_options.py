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
    unmatched_labels: list[str] = field(default_factory=list)
    figure_options: list[str] = field(default_factory=list)
    flagged_pages: list[int] = field(default_factory=list)


def backfill_paper_options(
    conn: psycopg.Connection,
    qp_pdf: Path,
    *,
    dry_run: bool = False,
) -> OptionsReport:
    """Extract and write option text for one already-loaded MCQ paper.

    Matches the vision pass's questions back to existing rows by paper slug +
    display label — the same identity the initial geometric load keyed on —
    rather than inserting anything new.
    """
    from .extract import cross_check, extract_page
    from .ingest import resolve_subject_slug
    from .load import replace_options

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

    report = OptionsReport(paper_slug=paper_slug)
    work = settings.work_dir / f"{paper_slug}-options"
    pages = render_pdf(qp_pdf, work)

    for page in pages:
        extracted = extract_page(page)
        if not extracted.is_content_page:
            continue
        if cross_check(page, extracted):
            report.flagged_pages.append(page.page_number)

        for question in extracted.questions:
            if question.question_type != "mcq" or not question.options:
                continue
            options = {k: v for k, v in question.options.items() if v and v.strip()}
            if len(options) < 2:
                continue

            question_id = existing.get(question.display_label)
            if question_id is None:
                report.unmatched_labels.append(question.display_label)
                continue

            report.matched += 1
            if any(v.strip().lower().startswith(_FIGURE_MARKER) for v in options.values()):
                report.figure_options.append(question.display_label)

            if not dry_run:
                replace_options(conn, question_id, options)
                report.written += 1
                if question.question_text and question.question_text.strip():
                    with conn.cursor() as cur:
                        cur.execute(
                            "update questions set question_text = %s"
                            " where id = %s and question_text is null",
                            (question.question_text.strip(), question_id),
                        )

    return report
