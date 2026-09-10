"""Deterministic segmentation of structured (non-MCQ) papers.

segment.py's whole argument for multiple-choice was that CAIE lays the paper
out on a fixed grid, so a question's boundary is a geometric fact rather than
something a model has to infer. Structured papers were assumed to need the
full vision pipeline instead, because they have no such grid — but they do
have something just as mechanical: a fixed left-margin *per level* of the
question hierarchy. The question number sits in the gutter; its parts are
indented past it; a part's own sub-parts are indented past that. Checked
against a real paper (physics-5054 2015 May/June Paper 2), the three margins
are consistent across all nine questions and both sections.

What this still cannot do without a person or a vision model is describe a
figure, or read a table's own structure — `question_text` will contain a
diagram's caption and whatever text sits over it, nothing more. Locating
where one part ends and the next begins does not need either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .segment import BODY_BOTTOM, BODY_TOP, CROP_LEFT, CROP_RIGHT, content_bottom, is_footer
from .worksheet import question_text as extract_text

# Indent bands, in points, for each level of the hierarchy: question number,
# part letter, sub-part roman numeral. Left generous — the exact pixel is a
# font-metrics detail that can drift a point or two between papers; a
# question's own number is always the leftmost bold thing on the page, and
# each level below it is always further right than the one above.
LEVEL_MAX_X = (60.0, 85.0, 118.0)

PART_RE = re.compile(r"^\(([a-z])\)$")
SUBPART_RE = re.compile(r"^\((i|ii|iii|iv|v|vi|vii|viii|ix|x)\)$", re.IGNORECASE)

# A trailing mark allocation, e.g. "..................[2]". Matched at the end
# of a leaf's own text so a bracket used mid-sentence for something else (an
# ion charge, a chemical formula) is never mistaken for a mark award.
MARKS_RE = re.compile(r"\[(\d{1,2})\]\s*$")

# CAIE's own dotted answer line. Real characters, not a drawn rule, so they
# come back from the text layer as a run of periods — collapsed here, since
# they are page furniture for handwriting, not the question's content.
DOTS_RE = re.compile(r"\.{4,}")

# An item's own label, printed at the very start of its own text — "1" opens
# with "1 Fig. 1.1 shows...", "(a)" opens with "(a) Use Fig. 1.1...". Stripped
# before storing: `display_label` already says this, so leaving it in the text
# too is noise in the search index, and worse, it is noise `validate_items`
# cannot tell apart from real content — a leaf whose only "text" is its own
# 3-character label ("(i)") would otherwise clear the emptiness floor for
# free.
_OWN_LABEL_RE = (
    re.compile(r"^\d{1,2}\b\s*"),
    re.compile(r"^\([a-z]\)\s*", re.IGNORECASE),
    re.compile(r"^\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\)\s*", re.IGNORECASE),
)

# A bare page number, nothing else. Distinct from "no text" (< 5pt tall, never
# reaches text extraction at all): this is a *printed* page number sitting
# alone in the sliver between a continued item's last line and the next
# item's marker on the following page — real content, but not this item's.
PAGE_NUMBER_ONLY_RE = re.compile(r"^\d+$")

# "Section A" / "Section B", centred rather than in any gutter. Not part of
# any question, but sitting between one question's last part and the next
# question's first one — extending a region straight through to the next
# marker would otherwise pull the heading (and Section B's own instructions)
# into the crop for whatever the last question of Section A was.
SECTION_RE = re.compile(r"^Section [A-Z]$")


@dataclass
class Marker:
    level: int  # 0 = question, 1 = part, 2 = sub-part
    label: str
    page_number: int
    y0: float


@dataclass
class SectionBreak:
    page_number: int
    y0: float


@dataclass
class StructuredItem:
    display_label: str
    parent_label: str | None
    level: int
    question_text: str
    max_marks: int | None
    # One entry per page the item's own content appears on, in reading order.
    regions: list[tuple[int, tuple[float, float, float, float]]]
    continues_on_next_page: bool = False
    is_continuation: bool = False


_LEVEL_PATTERNS = (
    re.compile(r"^\d{1,2}$"),
    PART_RE,
    SUBPART_RE,
)


def _level_for_x0(x0: float) -> int | None:
    for level, ceiling in enumerate(LEVEL_MAX_X):
        if x0 < ceiling:
            return level
    return None


def find_markers(page: pymupdf.Page) -> tuple[list[Marker], list[SectionBreak]]:
    """Question/part/sub-part labels on one page, plus any section heading.

    A label is the first span of its line: bold, sitting in one of the three
    indent bands, and matching that band's own shape (bare digits for a
    question, "(a)" for a part, "(i)" for a sub-part).

    Usually that span *is* one label — "(a)" alone, its own span, its own
    line. But CAIE sometimes sets two or three adjacent labels as a single
    run when nothing but whitespace separates them: "(a) (i) " is one span,
    and so is "10 (a) " when a question's first part starts on its own
    opening line. The span's own x0 says which level the *first* token in it
    is; every token after that is checked against the next level down, so a
    merged run still yields one marker per label instead of being missed
    entirely because the run as a whole matches no single pattern.
    """
    page_number = page.number + 1
    markers: list[Marker] = []
    breaks: list[SectionBreak] = []

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            span = spans[0]
            text = span["text"].strip()
            x0, y0 = span["bbox"][0], span["bbox"][1]
            if not (BODY_TOP < y0 < BODY_BOTTOM) or not text:
                continue

            if SECTION_RE.match(text) and x0 > 200:
                breaks.append(SectionBreak(page_number, y0))
                continue

            if "Bold" not in span["font"]:
                continue

            level = _level_for_x0(x0)
            if level is None:
                continue

            for token in text.split():
                if level > 2 or not _LEVEL_PATTERNS[level].match(token):
                    break
                label = token if level == 0 else token[1:-1].lower()
                markers.append(Marker(level, label, page_number, y0))
                level += 1

    markers.sort(key=lambda m: m.y0)
    breaks.sort(key=lambda b: b.y0)
    return markers, breaks


def _display_label(current: list[str | None], level: int) -> tuple[str, str | None]:
    if level == 0:
        return current[0] or "", None
    if level == 1:
        return f"{current[0]}({current[1]})", current[0] or None
    return f"{current[0]}({current[1]})({current[2]})", f"{current[0]}({current[1]})"


def page_bottom(page: pymupdf.Page) -> float:
    bottom = content_bottom(page, BODY_TOP, BODY_BOTTOM)
    return bottom if bottom is not None else BODY_BOTTOM


def segment_structured_paper(
    pdf_path: Path,
) -> tuple[list[StructuredItem], list[str]]:
    """Locate every question, part and sub-part in a structured paper.

    Returns the items — leaves only; a part with its own sub-parts carries no
    text of its own and is not returned, matching how marks are actually
    awarded — and a list of problems. A non-empty problem list means this
    paper does not match the template well enough to trust the boxes it
    produced; send it to a person instead of loading it.
    """
    problems: list[str] = []

    with pymupdf.open(pdf_path) as doc:
        pages = list(doc)
        all_markers: list[Marker] = []
        all_breaks: list[SectionBreak] = []
        for page in pages:
            markers, breaks = find_markers(page)
            all_markers.extend(markers)
            all_breaks.extend(breaks)

        if not all_markers:
            return [], ["no question markers found"]

        current: list[str | None] = [None, None, None]
        entries: list[tuple[str, str | None, Marker]] = []
        for marker in all_markers:
            current[marker.level] = marker.label
            for lvl in range(marker.level + 1, 3):
                current[lvl] = None
            label, parent = _display_label(current, marker.level)
            entries.append((label, parent, marker))

        items: list[StructuredItem] = []
        for i, (label, parent, marker) in enumerate(entries):
            next_marker = entries[i + 1][2] if i + 1 < len(entries) else None
            # A break sitting between this marker and the next one ends this
            # item's content early, on the page it falls on.
            cut = next(
                (b for b in all_breaks if _between(marker, b, next_marker)), None
            )

            regions: list[tuple[int, tuple[float, float, float, float]]] = []
            page_no = marker.page_number
            y0 = marker.y0
            while True:
                page = pages[page_no - 1]
                if cut is not None and cut.page_number == page_no:
                    y1 = cut.y0
                elif next_marker is not None and next_marker.page_number == page_no:
                    y1 = next_marker.y0
                elif next_marker is not None and next_marker.page_number > page_no:
                    y1 = page_bottom(page)
                else:
                    y1 = page_bottom(page)
                top = max(y0 - 2.0, BODY_TOP)
                # A trailing page whose only "content" is the gap before the
                # very next marker starts right at the top — the common case
                # when a multi-page item's real content ends earlier on the
                # previous page and this page contributes nothing of its own.
                # Geometry alone (a height floor) still passes a printed page
                # number sitting alone in that gap, which is real text but
                # not this item's — and rendering it pads past the box below,
                # visibly bleeding into the *next* item's own opening line.
                candidate = (CROP_LEFT, top, CROP_RIGHT, y1)
                if y1 - top > 5.0:
                    candidate_text = extract_text(doc, page_no, candidate).strip()
                    if candidate_text and not PAGE_NUMBER_ONLY_RE.match(candidate_text):
                        regions.append((page_no, candidate))

                spans_to_next_page = (
                    cut is None
                    and next_marker is not None
                    and next_marker.page_number > page_no
                )
                if not spans_to_next_page:
                    break
                page_no += 1
                y0 = BODY_TOP

            raw_text = " ".join(
                extract_text(doc, page_no, bbox) for page_no, bbox in regions
            )
            marks_match = MARKS_RE.search(raw_text)
            max_marks = int(marks_match.group(1)) if marks_match else None
            clean_text = DOTS_RE.sub("", raw_text).strip()
            clean_text = _OWN_LABEL_RE[marker.level].sub("", clean_text, count=1).strip()

            items.append(
                StructuredItem(
                    display_label=label,
                    parent_label=parent,
                    level=marker.level,
                    question_text=clean_text,
                    max_marks=max_marks,
                    regions=regions,
                    continues_on_next_page=len(regions) > 1,
                )
            )

    problems.extend(validate_items(items))
    return items, problems


def _between(start: Marker, candidate: SectionBreak, end: Marker | None) -> bool:
    lo = (start.page_number, start.y0)
    hi = (end.page_number, end.y0) if end is not None else (10**9, 0.0)
    point = (candidate.page_number, candidate.y0)
    return lo < point < hi


def validate_items(items: list[StructuredItem]) -> list[str]:
    """An empty sub-part is a geometry that did not actually land on the
    template — refuse it rather than load a blank card. A question or a part
    is allowed to be empty: CAIE often opens straight into "(a)" or "(i)"
    with no text of its own before the first child, and that container still
    needs a row for its children's `parent_question_id` and for the crop
    spanning the whole question to attach to."""
    problems: list[str] = []
    for item in items:
        if item.level == 2 and len(item.question_text) < 3:
            problems.append(f"{item.display_label}: no text found in its region")
    labels = [item.display_label for item in items]
    if len(labels) != len(set(labels)):
        dupes = sorted({label for label in labels if labels.count(label) > 1})
        problems.append(f"duplicate labels: {dupes}")
    return problems


def top_level_regions(
    items: list[StructuredItem],
) -> dict[str, list[tuple[int, tuple[float, float, float, float]]]]:
    """What a student is actually shown for one top-level question: every
    page its own content or any of its parts'/sub-parts' content touches,
    each page's own slice merged into one box.

    A sub-part's crop is its own narrow slice — useful for matching it to its
    mark-scheme entry, useless on its own for practice, since CAIE's parts
    routinely share one figure between them ('On Fig. 1.1, draw...' in 1(b)
    refers to the same diagram 1(a) already used). Grouped here by top-level
    number rather than sliced per level, so the figure stays with every part
    that needs it.
    """
    groups: dict[str, list[tuple[int, tuple[float, float, float, float]]]] = {}

    for item in items:
        top = item.display_label.split("(")[0]
        by_page: dict[int, list[float]] = {
            page: list(bbox) for page, bbox in groups.get(top, [])
        }
        for page, (x0, y0, x1, y1) in item.regions:
            if page not in by_page:
                by_page[page] = [x0, y0, x1, y1]
            else:
                current = by_page[page]
                current[0] = min(current[0], x0)
                current[1] = min(current[1], y0)
                current[2] = max(current[2], x1)
                current[3] = max(current[3], y1)
        groups[top] = [
            (page, tuple(bounds)) for page, bounds in sorted(by_page.items())
        ]

    return groups
