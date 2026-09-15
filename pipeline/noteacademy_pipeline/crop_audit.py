"""Catch an MCQ crop that hides one of its own options, before a student does.

Found the hard way: eight approved, published multiple-choice crops whose
bbox stopped short of the fourth option (`segment.py`, fixed 2026-09-10) --
in one case the *correct* answer, so the question was unanswerable as
displayed. The bug was invisible to every check the pipeline already ran,
because the bbox was geometrically valid, just too short: `validate_regions`
has no way to know a crop is missing content it was never told to expect.

The only thing that can actually catch this is re-deriving what the crop
*should* contain and comparing. This does that directly: recompute the
region with the current segmenter, from the same source PDF the stored bbox
came from, and check whether the recomputed box reveals something the stored
one did not. A geometric bug like this is never subtle once you know to look
for it -- the stored box either contains all four options or it does not --
so this is exact, not a heuristic scored on confidence.

Two ways a stored bbox can be caught short, both worth distinguishing in the
report:

  missing_option  the stored crop's own text is missing a whole option letter
                  (B, C or D -- see `_OPTION_RE`'s note on why not A) that the
                  recomputed box's text has. The crop is showing three options
                  or fewer.

  midline_cut     a line of real content starts inside the stored box but
                  ends past it -- an option cut off mid-sentence rather than
                  before it starts, which `missing_option` would not catch if
                  the option's own letter happened to render before the cut.

A bbox that only *shrank* under recomputation (the segmenter now correctly
excludes a footer artefact or a stray blank glyph -- see 0027's sibling fixes
in segment.py) is not reported: nothing is hidden from a student by a crop
that used to carry extra whitespace or page furniture.

Read-only. This finds candidates for a human to look at in the review queue,
the same as everything else the pipeline is unsure about -- it does not
re-render or re-approve anything itself, on the same reasoning `verify.py`
gives for not auto-applying its own second-opinion tagging pass: eight wrong
answers slipping through *because* of bulk approval is exactly the failure
this exists to catch, so it should not be the failure this creates too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pymupdf

from .naming import caie_filename
from .segment import QuestionRegion, is_footer, segment_mcq_paper

# B, C, D only, not A: a bare "a" is an ordinary English word and would flag
# almost every question that has any prose in it. B, C and D never occur as
# a stray word, so their presence at the start of a line is a reliable signal
# of an option label -- the same reasoning the original manual audit used.
_OPTION_RE = re.compile(r"(?m)(?:^|\n)\s*([BCD])[\s).]")

Kind = str  # "missing_option" | "midline_cut"


@dataclass
class SuspectMcqCrop:
    question_id: str
    subject_slug: str
    paper_slug: str
    display_label: str
    correct_option: str | None
    kind: Kind
    detail: str
    old_bbox: tuple[float, float, float, float]
    new_bbox: tuple[float, float, float, float]


@dataclass
class CropAuditReport:
    checked: int = 0
    suspects: list[SuspectMcqCrop] = field(default_factory=list)
    skipped_missing_pdf: list[str] = field(default_factory=list)


def _option_letters(page: pymupdf.Page, bbox: tuple[float, float, float, float]) -> set[str]:
    text = page.get_text("text", clip=pymupdf.Rect(*bbox))
    return set(_OPTION_RE.findall(text))


def _midline_cut(
    page: pymupdf.Page, old_bbox: tuple[float, float, float, float]
) -> str | None:
    """A line of real content that starts inside `old_bbox` but is cut off by
    its bottom edge -- an option sliced mid-sentence rather than omitted
    outright. Mirrors `content_bottom`'s own definition of "real content":
    footer spans and blank glyphs excluded, same as segment.py's fix."""
    top, bottom = old_bbox[1], old_bbox[3]
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if is_footer(span) or not span["text"].strip():
                    continue
                y0, y1 = span["bbox"][1], span["bbox"][3]
                if top < y0 < bottom < y1 - 1.0:
                    return span["text"].strip()
    return None


def classify_bbox(
    page: pymupdf.Page,
    old_bbox: tuple[float, float, float, float],
    new_bbox: tuple[float, float, float, float],
) -> tuple[Kind, str] | None:
    """Is the *stored* box (`old_bbox`) hiding something the recomputed one
    (`new_bbox`) would show? Returns (kind, detail) if so, else None.

    Order matters: a growth that reveals a wholly new option letter is
    reported as `missing_option` even if the boundary also happens to fall
    mid-line, because "an option is entirely absent" is the more direct and
    more serious of the two claims to make about the same crop.
    """
    if new_bbox[3] - old_bbox[3] > 1.0:
        old_letters = _option_letters(page, old_bbox)
        new_letters = _option_letters(page, new_bbox)
        revealed = new_letters - old_letters
        if revealed:
            return (
                "missing_option",
                f"stored crop shows {sorted(old_letters) or '(no options)'}, "
                f"recomputed shows {sorted(new_letters)}",
            )

    cut = _midline_cut(page, old_bbox)
    if cut is not None:
        return ("midline_cut", f"cut off mid-line: {cut!r}")

    return None


_QUERY = """
select
  q.id, q.display_label, q.correct_option,
  sub.slug as subject_slug, sub.syllabus_code,
  es.year, es.season,
  p.component, p.variant, p.slug as paper_slug,
  qa.bbox, qa.page_number
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
join exam_sessions es on es.id = p.exam_session_id
join question_assets qa on qa.question_id = q.id and qa.kind = 'question_crop'
where q.question_type = 'mcq'
  and q.extraction_status in ('approved', 'needs_review')
  and (%(subjects)s::text[] is null or sub.slug = any(%(subjects)s::text[]))
order by sub.slug, p.slug, q.ordinal
"""


def audit_mcq_crops(
    conn: psycopg.Connection,
    papers_dir: Path,
    subject_slugs: list[str] | None = None,
) -> CropAuditReport:
    """Recompute every approved or pending MCQ's crop region from its source
    PDF and report any that reveal more than the stored box does.

    One `segment_mcq_paper` call per PDF regardless of how many of its
    questions are checked -- a paper of 40 costs one parse, not 40.
    """
    report = CropAuditReport()
    region_cache: dict[Path, list[QuestionRegion]] = {}
    doc_cache: dict[Path, pymupdf.Document] = {}

    with conn.cursor() as cur:
        cur.execute(_QUERY, {"subjects": subject_slugs})
        rows = cur.fetchall()

    try:
        for row in rows:
            pdf_path = papers_dir / row["syllabus_code"] / caie_filename(
                row["syllabus_code"], row["year"], row["season"], "qp",
                row["component"], row["variant"],
            )
            if not pdf_path.exists():
                report.skipped_missing_pdf.append(str(pdf_path))
                continue

            if pdf_path not in region_cache:
                regions, problems = segment_mcq_paper(pdf_path)
                # A paper whose geometry no longer matches the template is not
                # this check's problem to diagnose -- skip it rather than
                # compare against regions that were refused.
                region_cache[pdf_path] = [] if problems else regions
                doc_cache[pdf_path] = pymupdf.open(pdf_path)

            try:
                question_number = int(row["display_label"])
            except ValueError:
                continue

            match = [r for r in region_cache[pdf_path] if r.number == question_number]
            if not match:
                continue

            report.checked += 1
            new_region = match[0]
            old_bbox = tuple(row["bbox"])
            page = doc_cache[pdf_path][new_region.page_number - 1]

            classification = classify_bbox(page, old_bbox, new_region.bbox)
            if classification is not None:
                kind, detail = classification
                report.suspects.append(
                    SuspectMcqCrop(
                        question_id=str(row["id"]),
                        subject_slug=row["subject_slug"],
                        paper_slug=row["paper_slug"],
                        display_label=row["display_label"],
                        correct_option=row["correct_option"],
                        kind=kind,
                        detail=detail,
                        old_bbox=old_bbox,
                        new_bbox=new_region.bbox,
                    )
                )
    finally:
        for doc in doc_cache.values():
            doc.close()

    return report
