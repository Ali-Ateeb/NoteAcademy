"""Multiple-choice ingestion: question paper plus mark scheme, into Postgres.

This is the whole MCQ path in one function, and it calls no model. Boundaries
come from page geometry, the answer key comes from the mark scheme's text
layer, and the display artifact is a crop of the page. Nothing here can invent a
question or an answer; the failure mode is refusing to load, which is the one
this project can live with.

What is deliberately *not* loaded: question and option text. Reading order in a
CAIE multiple-choice paper is scrambled — option letters extract after their
text, options come out D-C-B-A, and a 2015 circuit question has four circuit
diagrams where its options should be. Text from that is worse than no text: it
would look right in the database and be wrong on the screen. It arrives later,
from the vision pass, cross-checked. Until then a question is its crop, its
number and its answer, which is enough to sit the paper.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pymupdf

from .load import (
    PaperRef,
    attach_asset,
    clear_assets,
    record_document,
    resolve_paper_id,
    upsert_question,
)
from .markscheme import (
    match_mark_scheme,
    parse_mcq_answer_grid,
    parse_structured_mark_scheme,
    validate_answer_grid,
)
from .naming import PaperFile, crop_key, document_key, storage_prefix, structured_crop_key
from .render import crop
from .segment import segment_mcq_paper
from .segment_structured import segment_structured_paper, top_level_regions

log = logging.getLogger(__name__)


class _McqStub:
    """The shape `upsert_question` reads, for a question we have not read.

    Segmentation knows a question's label and nothing else about its content;
    the extraction schema is built for the vision path, which returns text.
    Rather than construct a half-populated ExtractedQuestion and have it look
    like a failed extraction, the absence is explicit.
    """

    question_type = "mcq"
    max_marks = 1
    question_text = None

    def __init__(self, display_label: str) -> None:
        self.display_label = display_label


@dataclass
class IngestReport:
    paper_slug: str = ""
    questions: int = 0
    with_answer: int = 0
    flagged: int = 0
    crops_written: int = 0
    crops_uploaded: int = 0
    # (storage_key, local file) for every crop written this run, so uploading is
    # a separate decision from segmenting and can be retried on its own.
    crops: list[tuple[str, Path]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def disagreements(qp: PaperFile, ms: PaperFile) -> list[str]:
    """Everything two filenames disagree about, as a reviewer would say it.

    The commonest ingestion mistake by a distance is a mark scheme from the
    right session and the wrong variant. Every answer it supplies is plausible
    and most of them are wrong, and nothing downstream can tell — the questions
    would look perfectly ingested and mark a student down for being right.
    """
    problems = [
        name
        for name, left, right in (
            ("syllabus", qp.syllabus_code, ms.syllabus_code),
            ("year", qp.year, ms.year),
            ("season", qp.season, ms.season),
            ("component", qp.component, ms.component),
            ("variant", qp.variant, ms.variant),
        )
        if left != right
    ]
    if qp.doc_type != "qp":
        problems.append(f"{qp.doc_type} given where a question paper was expected")
    if ms.doc_type != "ms":
        problems.append(f"{ms.doc_type} given where a mark scheme was expected")
    return problems


def file_digest(path: Path) -> str:
    """sha256 of a document, so a silent upstream republish is detectable."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def resolve_subject_slug(conn: psycopg.Connection, syllabus_code: str) -> str:
    """Find the subject a syllabus code belongs to.

    Codes are unique across CAIE's catalogue in practice, but the schema allows
    the same code at two levels, and picking one at random would file a paper
    under the wrong qualification. Two matches is a question for a human.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select slug from subjects where syllabus_code = %s order by slug",
            (syllabus_code,),
        )
        matches = [row["slug"] for row in cur.fetchall()]

    if not matches:
        raise LookupError(
            f"no subject with syllabus code {syllabus_code}. "
            "Load db/seed_catalog.sql, or pass the subject explicitly."
        )
    if len(matches) > 1:
        raise LookupError(
            f"syllabus code {syllabus_code} matches {matches}; pass one explicitly."
        )
    return matches[0]


def ingest_mcq_paper(
    conn: psycopg.Connection,
    *,
    qp_pdf: Path,
    ms_pdf: Path,
    paper: PaperFile,
    subject_slug: str,
    crops_dir: Path | None = None,
    crop_dpi: int = 150,
) -> IngestReport:
    """Load one multiple-choice sitting. Idempotent: re-running updates in place.

    Refuses before it writes. A paper whose geometry does not match the
    template, or whose answer grid does not parse, is a paper this path does not
    understand — and half a paper in the bank is worse than none, because the
    missing half is invisible.
    """
    report = IngestReport()

    if paper.component is None:
        report.problems.append(f"{qp_pdf.name} does not name a component")
        return report

    regions, problems = segment_mcq_paper(qp_pdf)
    if problems:
        report.problems.extend(problems)
        return report

    with pymupdf.open(ms_pdf) as doc:
        answers = parse_mcq_answer_grid([page.get_text("text") for page in doc])

    grid_problems = (
        validate_answer_grid(answers, len(regions)) if answers else ["no answer grid found"]
    )
    if grid_problems:
        # Not fatal on its own: a paper can be loaded with its questions flagged
        # for a reviewer to attach answers to. It is loud, though — an MCQ bank
        # without answers is a list of pictures.
        report.problems.extend(f"mark scheme: {problem}" for problem in grid_problems)

    prefix = storage_prefix(
        paper.syllabus_code, paper.year, paper.season, paper.component, paper.variant
    )

    paper_id = resolve_paper_id(
        conn,
        PaperRef(
            subject_slug=subject_slug,
            year=paper.year,
            season=paper.season,
            component=paper.component,
            variant=paper.variant,
            question_type="mcq",
        ),
    )

    for doc_type, path in (("qp", qp_pdf), ("ms", ms_pdf)):
        record_document(
            conn,
            paper_id,
            doc_type=doc_type,
            storage_key=document_key(prefix, doc_type),
            page_count=page_count(path),
            byte_size=path.stat().st_size,
            checksum=file_digest(path),
        )

    for ordinal, region in enumerate(regions, start=1):
        answer = answers.get(str(region.number))
        flags = [] if answer else ["unmatched_mark_scheme"]

        question_id = upsert_question(
            conn,
            paper_id,
            _McqStub(region.display_label),
            ordinal=ordinal,
            correct_option=answer,
            # Geometry either matched the template or the run was refused above,
            # so there is no uncertainty left to express here. This is not a
            # claim about the question's *text*, which has not been read.
            confidence=1.0,
            review_flags=flags,
        )
        if flags:
            report.flagged += 1
        if answer:
            report.with_answer += 1

        clear_assets(conn, question_id, "question_crop")
        attach_asset(
            conn,
            question_id,
            kind="question_crop",
            storage_key=crop_key(prefix, region.number),
            page_number=region.page_number,
            bbox=region.bbox,
        )
        report.questions += 1

        if crops_dir is not None:
            written = crop(
                qp_pdf,
                region.page_number,
                region.bbox,
                crops_dir / f"{region.number}.png",
                dpi=crop_dpi,
            )
            report.crops_written += 1
            report.crops.append((crop_key(prefix, region.number), written))

    with conn.cursor() as cur:
        cur.execute("select slug from papers where id = %s", (paper_id,))
        report.paper_slug = cur.fetchone()["slug"]

    return report


class _StructuredStub:
    """`_McqStub`'s counterpart for a question this path *has* read.

    Segmentation returns real text here — its own region's, read straight off
    the page — so there is no "not read yet" gap to mark. What is still
    missing is a figure's description, and that shows up as short or empty
    text on whatever item sits under the diagram, not as an absent field.
    """

    question_type = "structured"

    def __init__(
        self, display_label: str, question_text: str | None, max_marks: int | None
    ) -> None:
        self.display_label = display_label
        self.question_text = question_text
        self.max_marks = max_marks


def ingest_structured_paper(
    conn: psycopg.Connection,
    *,
    qp_pdf: Path,
    ms_pdf: Path,
    paper: PaperFile,
    subject_slug: str,
    crops_dir: Path | None = None,
    crop_dpi: int = 150,
) -> IngestReport:
    """Load one structured (Paper 2 style) sitting. Idempotent, like the MCQ path.

    Boundaries come from a different grid than a multiple-choice paper's, but
    it is still a grid: a question's indent puts it in the gutter, a part's
    indent puts it past that, and neither drifts across pages. Marks are read
    the same way the MCQ answer grid is, off the mark scheme's own text layer.
    A leaf whose label the mark scheme never mentions is loaded flagged, not
    guessed at — the same refusal `match_mark_scheme` already makes on its own.

    Every item lands as a row, containers included: a part with sub-parts of
    its own carries no marks and no crop, but its id is what those sub-parts'
    `parent_question_id` points at, and it is what the top-level crop (shared
    across every part that needs the same figure) attaches to.
    """
    report = IngestReport()

    if paper.component is None:
        report.problems.append(f"{qp_pdf.name} does not name a component")
        return report

    items, problems = segment_structured_paper(qp_pdf)
    if problems:
        report.problems.extend(problems)
        return report

    with pymupdf.open(ms_pdf) as doc:
        pages_text = [page.get_text("text") for page in doc]
    known_questions = {item.display_label for item in items if item.level == 0}
    entries = parse_structured_mark_scheme(pages_text, known_questions=known_questions)

    leaf_labels = {item.display_label for item in items} - {
        item.parent_label for item in items if item.parent_label
    }
    match = match_mark_scheme(sorted(leaf_labels), entries)

    prefix = storage_prefix(
        paper.syllabus_code, paper.year, paper.season, paper.component, paper.variant
    )

    paper_id = resolve_paper_id(
        conn,
        PaperRef(
            subject_slug=subject_slug,
            year=paper.year,
            season=paper.season,
            component=paper.component,
            variant=paper.variant,
            question_type="structured",
        ),
    )

    for doc_type, path in (("qp", qp_pdf), ("ms", ms_pdf)):
        record_document(
            conn,
            paper_id,
            doc_type=doc_type,
            storage_key=document_key(prefix, doc_type),
            page_count=page_count(path),
            byte_size=path.stat().st_size,
            checksum=file_digest(path),
        )

    question_ids: dict[str, str] = {}
    for ordinal, item in enumerate(items, start=1):
        entry = match.matched.get(item.display_label) if item.display_label in leaf_labels else None
        flags = (
            ["unmatched_mark_scheme"]
            if item.display_label in leaf_labels and entry is None
            else []
        )

        question_id = upsert_question(
            conn,
            paper_id,
            _StructuredStub(
                item.display_label,
                item.question_text or None,
                item.max_marks or (entry.marks if entry else None),
            ),
            ordinal=ordinal,
            parent_id=question_ids.get(item.parent_label) if item.parent_label else None,
            mark_scheme_text=entry.content if entry else None,
            confidence=1.0,
            review_flags=flags,
        )
        question_ids[item.display_label] = question_id
        report.questions += 1
        if flags:
            report.flagged += 1
        if entry is not None:
            report.with_answer += 1

    for label, regions in top_level_regions(items).items():
        question_id = question_ids[label]
        clear_assets(conn, question_id, "question_crop")
        for sort_order, (page_no, bbox) in enumerate(regions):
            attach_asset(
                conn,
                question_id,
                kind="question_crop",
                storage_key=structured_crop_key(prefix, label, sort_order),
                page_number=page_no,
                bbox=bbox,
                sort_order=sort_order,
            )
            if crops_dir is not None:
                suffix = "" if sort_order == 0 else f".{sort_order}"
                written = crop(
                    qp_pdf,
                    page_no,
                    bbox,
                    crops_dir / f"{label}{suffix}.png",
                    dpi=crop_dpi,
                )
                report.crops_written += 1
                report.crops.append(
                    (structured_crop_key(prefix, label, sort_order), written)
                )

    with conn.cursor() as cur:
        cur.execute("select slug from papers where id = %s", (paper_id,))
        report.paper_slug = cur.fetchone()["slug"]

    return report
