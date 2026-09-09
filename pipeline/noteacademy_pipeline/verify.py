"""A second, independent topic tag, checked against the first.

`worksheet.py`'s pass tags from text pulled out of the question paper's own PDF
layer — reading order scrambled, options frequently backwards, nothing at all
for a question whose content is a diagram. It works because the *stem* usually
carries enough words to classify on, but "usually" is not "verified", and the
600 tags it produced had not been looked at by anyone.

Reviewing all 1238 questions individually does not scale, and most of that
queue does not need it: the answer keys are parsed from a machine-readable
table and validate 40/40 on every paper, and the crops are geometrically
segmented with nothing shaped like an outlier. The one thing worth checking is
the tag, because it is the one judgement call in the pipeline.

So this hands the *crop* — the question as printed, the same thing a reviewer
would look at — to a second, independent pass, with the first pass's answer
withheld. Two independent methods agreeing is real evidence; recorded as a
confidence bump. Disagreeing is exactly the situation the review queue exists
for: both proposals are recorded, and the tag drops below the confidence floor
so a human is the one who breaks the tie — reusing the mechanism the pipeline
already has, rather than inventing a second one to explain.

Nothing here calls a model. It builds contact sheets of crops for someone to
look at — a person, or a Claude Code session with no API key, the same as the
first pass — and applies whatever they decide.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pymupdf
from PIL import Image, ImageDraw, ImageFont

from .naming import caie_filename
from .worksheet import question_text

# Below this many normalised characters, two questions are not compared for
# being duplicates: short stems ("Which is a vector?") recur by coincidence
# across unrelated questions, and a false match would silently apply one
# question's tag to a different one.
DEDUPE_MIN_LENGTH = 60

# Contact sheet layout. Crops render at 150 dpi (render.py's crop_dpi), which is
# far more resolution than reading a question stem needs; scaling down cuts the
# image tokens a viewer pays for without losing anything legible.
SHEET_WIDTH = 700
HEADER_HEIGHT = 28
ITEM_MARGIN = 10
DEFAULT_MAX_SHEET_HEIGHT = 5000


@dataclass
class TaggedQuestion:
    id: str
    paper_slug: str
    display_label: str
    ordinal: int
    syllabus_code: str
    year: int
    season: str
    component: int
    variant: int | None
    page_number: int | None
    bbox: tuple[float, float, float, float] | None
    primary_code: str | None
    primary_confidence: float | None


def load_tagged_questions(
    conn: psycopg.Connection, subject_slug: str
) -> list[TaggedQuestion]:
    """Every multiple-choice question in a subject that already carries a
    primary topic — the population a second pass has something to check."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.id, q.display_label, q.ordinal,
                   p.slug as paper_slug, p.component, p.variant,
                   es.year, es.season, sub.syllabus_code,
                   qa.page_number as crop_page, qa.bbox,
                   qt.confidence as primary_confidence, t.code as primary_code
              from questions q
              join papers p on p.id = q.paper_id
              join subjects sub on sub.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
              join question_topics qt on qt.question_id = q.id and qt.is_primary
              join topics t on t.id = qt.topic_id
              left join question_assets qa
                on qa.question_id = q.id and qa.kind = 'question_crop'
             where sub.slug = %s and q.question_type = 'mcq'
             order by es.year desc, p.slug, q.ordinal
            """,
            (subject_slug,),
        )
        rows = cur.fetchall()

    return [
        TaggedQuestion(
            id=str(row["id"]),
            paper_slug=row["paper_slug"],
            display_label=row["display_label"],
            ordinal=row["ordinal"],
            syllabus_code=row["syllabus_code"],
            year=row["year"],
            season=row["season"],
            component=row["component"],
            variant=row["variant"],
            page_number=row["crop_page"],
            bbox=tuple(row["bbox"]) if row["bbox"] else None,
            primary_code=row["primary_code"],
            primary_confidence=(
                float(row["primary_confidence"])
                if row["primary_confidence"] is not None
                else None
            ),
        )
        for row in rows
    ]


def _normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", ascii_only.lower())


def group_duplicates(
    questions: list[TaggedQuestion], papers_dir: Path
) -> tuple[list[TaggedQuestion], dict[str, list[str]]]:
    """Cluster questions with identical wording, so a second pass looks at each
    question once rather than once per variant it happens to appear in.

    CAIE variants of the same session share most of their multiple-choice
    items verbatim — component 11 and component 12 of one sitting are the
    common case — and viewing each twice would spend a reviewer's attention on
    nothing new. Matched on the extracted stem text rather than the crop
    image, because two variants render the same question from two different
    source PDFs and a pixel-for-pixel comparison would miss a match that
    differs only in anti-aliasing.

    Returns the canonical question to show (one per group, the newest sitting)
    and a map from that question's id to every id — canonical included — that
    should receive the same decision.
    """
    texts: dict[str, str] = {}
    opened: dict[Path, pymupdf.Document] = {}

    try:
        for question in questions:
            if not question.bbox or not question.page_number:
                continue
            name = caie_filename(
                question.syllabus_code, question.year, question.season, "qp",
                question.component, question.variant,
            )
            path = papers_dir / question.syllabus_code / name
            if not path.is_file():
                continue
            if path not in opened:
                opened[path] = pymupdf.open(path)
            raw = question_text(opened[path], question.page_number, question.bbox)
            texts[question.id] = _normalise(raw)
    finally:
        for doc in opened.values():
            doc.close()

    by_text: dict[str, list[TaggedQuestion]] = {}
    for question in questions:
        text = texts.get(question.id, "")
        # Below the threshold, or missing entirely (no crop on disk): treated
        # as its own group of one, never merged with anything.
        key = text if len(text) >= DEDUPE_MIN_LENGTH else f"__solo__{question.id}"
        by_text.setdefault(key, []).append(question)

    canonical: list[TaggedQuestion] = []
    groups: dict[str, list[str]] = {}
    for members in by_text.values():
        # Newest sitting first, so the canonical crop shown is the one most
        # likely to use current notation and units.
        members.sort(key=lambda q: (q.year, q.paper_slug, q.ordinal), reverse=True)
        head = members[0]
        canonical.append(head)
        groups[head.id] = [m.id for m in members]

    canonical.sort(key=lambda q: (-q.year, q.paper_slug, q.ordinal))
    return canonical, groups


def pack_by_height(heights: list[int], max_total: int) -> list[list[int]]:
    """Group indices into bins whose heights sum to at most `max_total`.

    Pure and side-effect free on purpose: this is the part of laying out a
    contact sheet worth testing without dragging PIL or a PDF into a test, and
    the packing itself has a real failure mode — a single item taller than
    `max_total` must still get a bin of its own rather than being dropped.
    """
    bins: list[list[int]] = []
    current: list[int] = []
    total = 0
    for i, height in enumerate(heights):
        if current and total + height > max_total:
            bins.append(current)
            current, total = [], 0
        current.append(i)
        total += height
    if current:
        bins.append(current)
    return bins


def _local_crop_path(work_root: Path, question: TaggedQuestion) -> Path | None:
    """Find the crop PNG `load-mcq --crops` wrote for this question.

    Two directory-naming conventions exist in this project's own history —
    `<code>_<season><yy>_qp_<paper>` from the bulk backfill, and
    `<code>_<season><yy>_<paper>` (no `_qp`) from the three papers ingested by
    hand before that convention settled — so both are tried rather than
    assuming the newer one everywhere.
    """
    name = caie_filename(
        question.syllabus_code, question.year, question.season, "qp",
        question.component, question.variant,
    )
    stem = name.removesuffix(".pdf")
    for candidate_dir in (stem, stem.replace("_qp_", "_")):
        path = work_root / candidate_dir / "crops" / f"{question.display_label}.png"
        if path.is_file():
            return path
    return None


def _font(size: int) -> ImageFont.ImageFont:
    # A real typeface if one is on disk; the bitmap fallback is legible enough
    # for a short label either way, so a missing font is not worth failing on.
    for candidate in (
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


@dataclass
class SheetItem:
    tag: str
    canonical_id: str
    paper_slug: str
    display_label: str
    group: list[str]


@dataclass
class BuildReport:
    sheets: list[Path] = field(default_factory=list)
    items_shown: int = 0
    duplicates_folded: int = 0
    missing_crop: list[str] = field(default_factory=list)


def build_verification_sheets(
    conn: psycopg.Connection,
    subject_slug: str,
    papers_dir: Path,
    out_dir: Path,
    *,
    target_width: int = SHEET_WIDTH,
    max_sheet_height: int = DEFAULT_MAX_SHEET_HEIGHT,
) -> BuildReport:
    """Build the contact sheets and manifest for a second tagging pass.

    The manifest never carries the first pass's tag. That is not an
    implementation detail — it is the entire point of a *second, independent*
    pass, and the temptation to look it up "just to check" is exactly what a
    file on disk makes easy. Keep the comparison for `apply_verification`,
    after the decision is already made.
    """
    report = BuildReport()
    questions = load_tagged_questions(conn, subject_slug)
    canonical, groups = group_duplicates(questions, papers_dir)
    report.duplicates_folded = len(questions) - len(canonical)

    work_root = Path("pipeline/work")
    sheets_dir = out_dir / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)

    loaded: list[tuple[TaggedQuestion, Image.Image]] = []
    for question in canonical:
        path = _local_crop_path(work_root, question)
        if path is None:
            report.missing_crop.append(question.id)
            continue
        with Image.open(path) as source:
            image = source.convert("RGB")
        scale = target_width / image.width
        scaled = image.resize(
            (target_width, max(1, round(image.height * scale))), Image.LANCZOS
        )
        loaded.append((question, scaled))

    heights = [HEADER_HEIGHT + img.height + ITEM_MARGIN for _, img in loaded]
    bins = pack_by_height(heights, max_sheet_height)

    header_font = _font(16)
    manifest_sheets: list[dict] = []

    for sheet_index, indices in enumerate(bins, start=1):
        canvas_height = sum(heights[i] for i in indices) + ITEM_MARGIN
        canvas = Image.new("RGB", (target_width + 2 * ITEM_MARGIN, canvas_height), "white")
        draw = ImageDraw.Draw(canvas)

        y = ITEM_MARGIN
        entries: list[dict] = []
        for item_index, i in enumerate(indices, start=1):
            question, image = loaded[i]
            tag = f"S{sheet_index}.{item_index}"

            draw.rectangle(
                [0, y, canvas.width, y + HEADER_HEIGHT], fill=(30, 30, 40)
            )
            label = f"{tag}   {question.paper_slug}  Q{question.display_label}"
            draw.text((ITEM_MARGIN, y + 5), label, fill="white", font=header_font)
            y += HEADER_HEIGHT

            canvas.paste(image, (ITEM_MARGIN, y))
            y += image.height + ITEM_MARGIN

            entries.append(
                {
                    "tag": tag,
                    "canonical_id": question.id,
                    "paper": question.paper_slug,
                    "label": question.display_label,
                    "group": groups[question.id],
                }
            )
            report.items_shown += 1

        sheet_path = sheets_dir / f"sheet-{sheet_index:03d}.jpg"
        canvas.save(sheet_path, "JPEG", quality=85)
        report.sheets.append(sheet_path)
        manifest_sheets.append({"path": str(sheet_path), "items": entries})

    manifest = {"subject": subject_slug, "sheets": manifest_sheets}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return report


@dataclass
class VerifyReport:
    agreed: int = 0
    disagreed: int = 0
    skipped_no_decision: int = 0
    skipped_approved: int = 0
    unknown_codes: list[str] = field(default_factory=list)


def apply_verification(
    conn: psycopg.Connection,
    manifest: dict,
    decisions: dict[str, str],
    *,
    confidence_floor: float,
) -> VerifyReport:
    """Compare the second pass's decisions to the first, and act on the result.

    Agreement raises the tag's confidence — two independent readings landing
    on the same topic is more evidence than either alone. Disagreement pushes
    it below the floor and records the second opinion as a secondary topic, so
    the reviewer who eventually looks at it sees both proposals rather than a
    bare "unsure".
    """
    report = VerifyReport()

    with conn.cursor() as cur:
        cur.execute(
            """
            select t.code, t.id
              from topics t
              join syllabus_versions sv on sv.id = t.syllabus_version_id
              join subjects s on s.id = sv.subject_id
             where s.slug = %s and sv.is_current
            """,
            (manifest["subject"],),
        )
        topic_ids = {row["code"]: row["id"] for row in cur.fetchall()}

    for sheet in manifest["sheets"]:
        for item in sheet["items"]:
            tag = item["tag"]
            if tag not in decisions:
                report.skipped_no_decision += len(item["group"])
                continue

            pass2_code = decisions[tag]
            if pass2_code not in topic_ids:
                report.unknown_codes.append(pass2_code)
                continue

            for question_id in item["group"]:
                _apply_one(conn, question_id, pass2_code, topic_ids, confidence_floor, report)

    return report


def _apply_one(
    conn: psycopg.Connection,
    question_id: str,
    pass2_code: str,
    topic_ids: dict[str, str],
    floor: float,
    report: VerifyReport,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select t.code, qt.confidence, q.extraction_status
              from question_topics qt
              join topics t on t.id = qt.topic_id
              join questions q on q.id = qt.question_id
             where qt.question_id = %s and qt.is_primary
            """,
            (question_id,),
        )
        row = cur.fetchone()
        if row is None:
            report.skipped_no_decision += 1
            return

        pass1_code = row["code"]
        pass1_confidence = float(row["confidence"])
        approved = row["extraction_status"] == "approved"

        if pass1_code == pass2_code:
            # Not simply "high confidence now" — bounded below by whatever the
            # first pass already believed, so a first pass that was already
            # more confident than a bare confirmation implies is not lowered.
            new_confidence = max(pass1_confidence, 0.9)
            cur.execute(
                "update question_topics set confidence = %s"
                " where question_id = %s and is_primary",
                (new_confidence, question_id),
            )
            if not approved:
                # A question flagged only for this reason is no longer unsure
                # of its topic; one flagged for something else too stays in
                # the queue, just without a stale reason attached to it.
                cur.execute(
                    "update questions set review_flags ="
                    " array_remove(review_flags, 'low_tag_confidence')"
                    " where id = %s",
                    (question_id,),
                )
                cur.execute(
                    "update questions set extraction_status = 'extracted'"
                    " where id = %s and extraction_status = 'needs_review'"
                    "   and cardinality(review_flags) = 0",
                    (question_id,),
                )
            report.agreed += 1
        else:
            # Below the floor regardless of how confident the first pass was:
            # a second independent method landing somewhere else is new
            # information a single confident guess did not have.
            new_confidence = min(pass1_confidence, floor - 0.01)
            cur.execute(
                "update question_topics set confidence = %s"
                " where question_id = %s and is_primary",
                (new_confidence, question_id),
            )
            cur.execute(
                """
                insert into question_topics
                  (question_id, topic_id, confidence, source, is_primary)
                values (%s, %s, %s, 'model', false)
                on conflict (question_id, topic_id) do update set
                  confidence = excluded.confidence
                """,
                (question_id, topic_ids[pass2_code], 0.6),
            )
            if not approved:
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
                    (question_id,),
                )
            report.disagreed += 1

        if approved:
            report.skipped_approved += 1
