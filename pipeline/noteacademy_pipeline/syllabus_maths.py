"""Read a CAIE mathematics syllabus into the same topic tree as the sciences.

The science syllabuses list numbered learning objectives in a gutter. The
mathematics ones set each topic as a two-column table:

    1.4  Fractions, decimals and percentages          Notes and examples
    1  Use the language and notation of ...           Candidates are expected ...
    2  Recognise equivalence and convert ...          Recurring decimal notation ...

The left column is the content that is examined and is what becomes the topic's
learning objectives, exactly as in the other subjects. The right column
("Notes and examples") is guidance and worked examples; it is not an objective,
and it is where nearly all the typeset equations are, so leaving it out also
leaves out the part of the page that extracts worst.

Geometry, stated rather than inferred (measured on the 2025-2027 syllabus, and
the same template is used across the mathematics syllabuses):

    section          13pt bold   number at x=62, title at x=96      '1   Number'
    topic            10pt bold   number at x=62, title at x=96      '1.4  Fractions, ...'
    left column      x < 300     objective text; a numbered objective starts at x=62
    right column     x >= 300    'Notes and examples' -- ignored

An objective is either numbered ('1 Use ...', continued on indented lines), or,
when a topic has no numbers, the topic's single statement with its bullet list
folded in ('Identify and use: natural numbers; integers; ...').
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .syllabus import (
    CHAPTER_SIZE,
    CONTINUED,
    SECTION_SIZE,
    Syllabus,
    Topic,
    _read_cover,
    _read_years,
    _reuse_or_add,
    validate,
)

LEFT_COLUMN_MAX_X = 300.0     # the right column starts at x=309
TITLE_MIN_X = 90.0            # section/topic titles start at x=96, numbers at x=62
BULLET_TEXT_MIN_X = 90.0      # bullet text starts at x=96, its glyph at x=79
HEADER_MAX_Y = 40.0           # running header
FOOTER_MIN_Y = 800.0          # page number, links
ROW_TOLERANCE = 3.0

NUMBER_ONLY = re.compile(r"^\d+$")
TOPIC_NUMBER = re.compile(r"^\d+\.\d+$")
# '1' followed by whatever glyph CAIE's font uses for the full stop ('1?' as
# extracted), then the text.
NUMBERED_OBJECTIVE = re.compile(r"^(\d{1,2})\W+(\S.*)$")
CONTENT_CHAPTER = re.compile(r"^\d+\W*\s*Subject content", re.IGNORECASE)
# A bullet glyph on a line by itself, in a private-use or symbol code point.
LONE_GLYPH = re.compile(r"^[^\w\s]$|^\W{1,2}$")


def is_mathematics_layout(pdf_path: Path) -> bool:
    """True for the two-column 'Notes and examples' template."""
    with pymupdf.open(pdf_path) as doc:
        headers = sum(page.get_text().count("Notes and examples") for page in doc)
    return headers >= 5


def _lines(page: pymupdf.Page) -> list[dict]:
    """Text lines with position, size and weight, running header and footer out."""
    lines: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            text = "".join(span["text"] for span in spans).strip()
            if not text:
                continue
            x0, y0, _, _ = line["bbox"]
            if y0 < HEADER_MAX_Y or y0 > FOOTER_MIN_Y:
                continue
            first = spans[0]
            lines.append(
                {
                    "x": x0,
                    "y": y0,
                    "size": first["size"],
                    "bold": bool(first["flags"] & 16),
                    "text": text,
                }
            )
    return lines


def _heading_rows(lines: list[dict]) -> list[dict]:
    """Bold heading lines with their title joined on: a number at x=62 and the
    title beside it at x=96 are two extracted lines on one baseline."""
    rows: list[dict] = []
    for line in lines:
        if not line["bold"] or line["x"] >= LEFT_COLUMN_MAX_X:
            continue
        if not (NUMBER_ONLY.match(line["text"]) or TOPIC_NUMBER.match(line["text"])):
            continue
        title = [
            other["text"]
            for other in lines
            if other["bold"]
            and abs(other["y"] - line["y"]) <= ROW_TOLERANCE
            and TITLE_MIN_X <= other["x"] < LEFT_COLUMN_MAX_X
        ]
        rows.append(
            {
                "code": line["text"],
                "title": " ".join(title).strip(),
                "y": line["y"],
                "size": line["size"],
            }
        )
    return rows


def _objectives(lines: list[dict]) -> list[str]:
    """The left-column text of one topic on one page, as objectives.

    Read in the PDF's own order, not sorted by height: a wrapped line that
    follows a superscript ("A x 10^n where n is a" / "positive or negative
    integer") sits *higher* than the line it continues, so sorting by y puts a
    sentence's tail before its head. The document order is the reading order.
    """
    objectives: list[str] = []
    after_bullet = False
    for line in lines:
        if line["x"] >= LEFT_COLUMN_MAX_X:
            continue
        text = line["text"]
        if LONE_GLYPH.match(text):
            after_bullet = True  # a bullet glyph on its own line; its text follows
            continue
        # Matrix and bracket pieces of a typeset vector extract as one or two
        # stray letters ("J", "KK", "OO"). No objective is one or two characters.
        if len(text) <= 2 and not NUMBERED_OBJECTIVE.match(text):
            continue
        numbered = NUMBERED_OBJECTIVE.match(text) if line["x"] < 70 else None
        if numbered:
            objectives.append(numbered.group(2))
        elif not objectives:
            objectives.append(text)
        elif after_bullet:
            joiner = " " if objectives[-1].endswith(":") else "; "
            objectives[-1] = f"{objectives[-1]}{joiner}{text}"
        else:
            objectives[-1] = f"{objectives[-1]} {text}"
        after_bullet = False
    # Doubled capitals (OO, KK) are the two halves of a drawn column-vector
    # bracket; a real vector name (AB) is two different letters.
    return [re.sub(r"\s+([A-Z])\1\b", "", objective) for objective in objectives]


def parse_mathematics_syllabus(pdf_path: Path) -> Syllabus:
    result = Syllabus()

    with pymupdf.open(pdf_path) as doc:
        result.subject_title, result.syllabus_code = _read_cover(doc)
        result.label, result.first_exam_year, result.last_exam_year = _read_years(doc)

        in_content = False
        section: Topic | None = None
        topic: Topic | None = None

        for page in doc:
            lines = _lines(page)

            # Chapter headings are the only 18pt text.
            for line in lines:
                if line["size"] >= CHAPTER_SIZE and line["bold"]:
                    in_content = bool(CONTENT_CHAPTER.match(line["text"]))
            if not in_content:
                continue

            headings = sorted(_heading_rows(lines), key=lambda row: row["y"])
            # Topic headings on this page, each owning the lines beneath it up
            # to the next heading; lines above the first one continue the
            # topic the previous page ended on.
            boundaries = [row["y"] for row in headings] + [float("inf")]
            current_lines = [line for line in lines if line["y"] < boundaries[0]]

            def flush(target: Topic | None, chunk: list[dict]) -> None:
                if target is None or target.children:
                    return
                body = [
                    line
                    for line in chunk
                    if not (line["bold"] and line["size"] >= SECTION_SIZE)
                    and not (line["bold"] and line["text"] == "Notes and examples")
                ]
                target.learning_objectives.extend(_objectives(body))

            flush(topic, [line for line in current_lines if line["size"] < SECTION_SIZE])

            for index, row in enumerate(headings):
                raw_title = row["title"]
                continued = bool(re.search(r"\(continued\)\s*$", raw_title))
                title = CONTINUED.sub("", re.sub(r"\s*\(continued\)\s*$", "", raw_title))
                if row["size"] >= SECTION_SIZE and NUMBER_ONLY.match(row["code"]):
                    section = _reuse_or_add(result.sections, row["code"], title)
                    if not continued:
                        topic = None
                elif TOPIC_NUMBER.match(row["code"]) and section is not None:
                    topic = _reuse_or_add(section.children, row["code"], title)
                else:
                    continue
                # Whatever sits under this heading, before the next one, belongs
                # to the topic now current: for a "(continued)" section heading
                # that is the topic the previous page ended on.
                chunk = [
                    line
                    for line in lines
                    if row["y"] + ROW_TOLERANCE < line["y"] < boundaries[index + 1] - 0.01
                    and line["size"] < SECTION_SIZE
                ]
                flush(topic, chunk)

    result.problems = validate(result)
    return result
