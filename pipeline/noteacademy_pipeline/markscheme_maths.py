"""Read a CAIE mathematics mark scheme: a four-column table, by geometry.

    Question | Answer | Marks | Partial Marks
    4(b)(i)  | 1 2 3 x y ... oe final answer | 2 | B1 for correct answer seen and spoilt

The science mark schemes this pipeline already reads are prose it can take line
by line. This one cannot be: the Marks column is a bare number ("2", "3"), which
the line-based reader cannot tell from a question number, and the Answer column
is typeset maths that extracts as scattered fragments in whatever order the
blocks were drawn. So it is read as a table, using where things sit on the page.

The table's layout has changed between series, so nothing is hard-coded to one:

    label column     x < 105. 2016-17 prints "10", "(a)", "(i)" as separate
                     tokens; 2019 prints "1(a)" and sometimes a bare "2"; 2024
                     prints "8(a)(iii)(b)". All three are read into one path.
    answer column    105 <= x < the marks boundary
    marks column     starts where the page's own "Mark"/"Marks" column title
                     starts (it moves: 268, 287, 318 across series). Its first
                     number is the row's marks ("2", "2*"); a row marked with
                     codes only ("M1" then "A1") is worth the sum of them.
    partial marks    the rest of the marks column

A paper's deepest label is three levels (8(a)(iii)). A lettered item inside a
sub-part is printed in the mark scheme as its own row, 8(a)(iii)(a) and
8(a)(iii)(b); the question-paper segmenter keeps such an item inside its
sub-part's own text, so those rows are folded into the sub-part: content joined
in order, marks added.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .schemas import MarkSchemeEntry

LABEL_MAX_X = 105.0
HEADER_MAX_Y = 52.0      # 'PUBLISHED' banner and the running page header (a landscape
                         # page's table title sits at ~56, so no higher than this)
FOOTER_MIN_Y = 790.0
ROW_TOLERANCE = 3.0
MARKS_TITLE_SLACK = 3.0  # data sits at or right of the title's left edge
HEADER_ROW_TOLERANCE = 4.0

ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
HEADER_WORDS = {
    "Question", "Answer", "Answers", "Mark", "Marks", "Part", "Partial Marks", "Part marks",
}
MARKS_TITLES = {"Mark", "Marks"}

QUESTION = re.compile(r"^\d{1,2}$")
COMBINED = re.compile(r"^(\d{1,2})((?:\([a-z]{1,4}\))+)$")
GROUP = re.compile(r"^\(([a-z]{1,4})\)$")
LEADING_NUMBER = re.compile(r"^(\d{1,2})(?:\s*\*|ft)?(?:\s|$)")
MARK_CODE = re.compile(r"\b[MABC](\d)(?:FT|dep)?\b")
PARTIAL_HEADER = re.compile(r"Part(?:ial)? [Mm]arks")


def is_mathematics_mark_scheme(pdf_path: Path) -> bool:
    with pymupdf.open(pdf_path) as doc:
        # One page is enough: the science schemes never print this column title,
        # and the older series print it on the first page of the table only.
        return any(PARTIAL_HEADER.search(page.get_text()) for page in doc)


def _fold_label(label: str) -> str:
    """8(a)(iii)(b) -> 8(a)(iii): nothing is deeper than a sub-part."""
    groups = re.findall(r"\([a-z]{1,4}\)", label)
    if len(groups) <= 2:
        return label
    return re.match(r"^\d+", label).group() + "".join(groups[:2])


def _next_path(token: str, path: list[str]) -> list[str] | None:
    """The label path after reading one label-column token, or None if it is
    not a label."""
    # "11 (a)" and "12 (a) (i)": the older series space the groups out.
    if re.match(r"^\d{1,2}\s*\(", token):
        token = re.sub(r"\s+", "", token)
    # "(c) (i)": a part and its first sub-part in one token, read a group at a time.
    compact = re.sub(r"\s+", "", token)
    if path and re.fullmatch(r"(?:\([a-z]{1,4}\)){2,}", compact):
        result: list[str] | None = path
        for group_letters in re.findall(r"\(([a-z]{1,4})\)", compact):
            result = _next_path(f"({group_letters})", result or [])
            if result is None:
                return None
        return result
    if QUESTION.match(token):
        return [token]
    combined = COMBINED.match(token)
    if combined:
        return [combined.group(1)] + re.findall(r"\(([a-z]{1,4})\)", combined.group(2))
    group = GROUP.match(token)
    if group and path:
        letters = group.group(1)
        if len(path) == 1:
            return path + [letters]
        # A roman numeral after a part is its first sub-part; after a sub-part,
        # its sibling. A letter is the next part.
        if letters in ROMAN:
            return path[:2] + [letters]
        return path[:1] + [letters]
    return None


def _marks_boundary(lines: list[dict]) -> float | None:
    """Where this page's Marks column starts, from its own header row, or None
    on a page with no table header.

    The header is not laid out the same way twice. Mostly "Marks" is a title of
    its own; the 2020 series prints one line, "Marks Partial Marks", whose left
    edge is the column's; and the 2016 series prints "Part" where the Marks title
    should be, so the reliable fact is that the marks column is the one
    before the last.
    """
    question = next(
        (ln for ln in lines if ln["text"] == "Question" and ln["x"] < LABEL_MAX_X), None
    )
    if question is None:
        return None
    header = sorted(
        (ln for ln in lines if abs(ln["y"] - question["y"]) <= HEADER_ROW_TOLERANCE),
        key=lambda ln: ln["x"],
    )
    for line in header:
        if PARTIAL_HEADER.search(line["text"]) and line["text"].split()[0] in MARKS_TITLES:
            return line["x"] - 1.0
    for line in header:
        if line["text"] in MARKS_TITLES:
            return line["x"] + MARKS_TITLE_SLACK
    if len(header) >= 4:
        return header[-2]["x"] + MARKS_TITLE_SLACK
    return None


def _row_marks(marks_lines: list[str]) -> int | None:
    if not marks_lines:
        return None
    number = LEADING_NUMBER.match(marks_lines[0])
    if number:
        return int(number.group(1))
    # Marked by codes only: "M1" on one line, "A1" on the next. Every line, not
    # just the first -- but only here, since a numbered row's partial-marks text
    # ("B1 for ...") describes how part of its marks are earned, not extra ones.
    codes = [digit for line in marks_lines for digit in MARK_CODE.findall(line)]
    return sum(int(digit) for digit in codes) if codes else None


def _scan(pdf_path: Path) -> tuple[list[dict], dict[int, dict]]:
    """Every table row in the mark scheme, in order, and the geometry of each
    page that held any: where its table starts, where its content ends, how
    wide it is. Both the text and the row images are read from this."""
    rows: list[dict] = []
    geometry: dict[int, dict] = {}
    path: list[str] = []
    boundary: float | None = None

    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            lines = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    # A landscape page reports its text in the unrotated frame;
                    # the table is read the way it is displayed.
                    box = pymupdf.Rect(line["bbox"])
                    if page.rotation:
                        box = box * page.rotation_matrix
                    x0, y0 = box.x0, box.y0
                    if text and HEADER_MAX_Y < y0 < FOOTER_MIN_Y:
                        lines.append({"x": x0, "y": y0, "y1": box.y1, "text": text})

            # Where this page's Marks column starts. The general marking
            # principles before the table have no such title, and are a
            # numbered list shaped exactly like question numbers, so nothing is
            # read until the first page that has one.
            found = _marks_boundary(lines)
            if found is not None:
                boundary = found
            if boundary is None:
                continue
            header = next(
                (ln for ln in lines if ln["text"] == "Question" and ln["x"] < LABEL_MAX_X), None
            )

            lines = [
                line
                for line in lines
                if line["text"] not in HEADER_WORDS and not PARTIAL_HEADER.search(line["text"])
            ]
            lines.sort(key=lambda line: (round(line["y"] / ROW_TOLERANCE), line["x"]))
            geometry[page.number + 1] = {
                # Below the column titles when the page has them, else below the
                # running header.
                "top": header["y1"] + 3.0 if header else HEADER_MAX_Y + 4.0,
                "bottom": min((max(ln["y1"] for ln in lines) if lines else 0) + 4.0,
                              FOOTER_MIN_Y - 2.0),
                "width": page.rect.width,
            }

            for line in lines:
                if line["x"] < LABEL_MAX_X:
                    updated = _next_path(line["text"], path)
                    if updated is not None:
                        path = updated
                        rows.append(
                            {
                                "label": path[0] + "".join(f"({p})" for p in path[1:]),
                                "answer": [],
                                "marks": [],
                                "page": page.number + 1,
                                "y": line["y"],
                            }
                        )
                        continue
                if rows:
                    column = "answer" if line["x"] < boundary else "marks"
                    rows[-1][column].append(line["text"])

    return rows, geometry


def parse_mathematics_mark_scheme(pdf_path: Path) -> list[MarkSchemeEntry]:
    rows, _ = _scan(pdf_path)

    merged: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        label = _fold_label(row["label"])
        answer = " ".join(row["answer"]).strip()
        marks_lines = row["marks"]
        marks = _row_marks(marks_lines)
        partial = ""
        if marks_lines:
            first = marks_lines[0]
            number = LEADING_NUMBER.match(first)
            rest = first[number.end():].strip() if number else first
            partial = " ".join(([rest] if rest else []) + marks_lines[1:]).strip()

        text = answer
        if marks is not None:
            text = f"{text} [{marks}]".strip()
        if partial:
            text = f"{text} {partial}".strip()

        if label not in merged:
            merged[label] = {"content": [], "marks": None}
            order.append(label)
        if text:
            merged[label]["content"].append(text)
        if marks is not None:
            merged[label]["marks"] = (merged[label]["marks"] or 0) + marks

    return [
        MarkSchemeEntry(
            display_label=label,
            marks=merged[label]["marks"],
            content=" ".join(merged[label]["content"]),
        )
        for label in order
        if merged[label]["content"]
    ]


Region = tuple[int, tuple[float, float, float, float]]

CROP_MARGIN_X = 30.0     # keep the table's own left and right edges out of the picture
ROW_TOP_PAD = 5.0
ROW_GAP = 6.0
MIN_ROW_HEIGHT = 8.0
MERGE_GAP = 3.0


def mark_scheme_regions(pdf_path: Path) -> dict[str, list[Region]]:
    """Where each answer sits on the page, as a picture to show a student.

    The Answer column is typeset maths, and it extracts as scattered fragments
    ("??" for the symbols), so the text `parse_mathematics_mark_scheme` returns
    is a search index, not something to read. The row itself, rendered, is the
    answer.

    Keyed by the same label as the text entries: rows folded into a sub-part
    (8(a)(iii)(a), 8(a)(iii)(b)) become one entry, and rows that are touching
    become one region. A row that carries on to the next page gets a second
    region there, from the top of that page's table to the first row of its own.
    Boxes are in the page as displayed, which is what a render's `clip` takes.
    """
    rows, geometry = _scan(pdf_path)

    by_page: dict[int, list[int]] = {}
    for index, row in enumerate(rows):
        by_page.setdefault(row["page"], []).append(index)

    raw: dict[int, list[Region]] = {index: [] for index in range(len(rows))}
    previous_row: int | None = None
    for page_number in sorted(by_page):
        page = geometry[page_number]
        starts = sorted(by_page[page_number], key=lambda i: rows[i]["y"])
        left, right = CROP_MARGIN_X, page["width"] - CROP_MARGIN_X

        first_y = rows[starts[0]]["y"]
        if previous_row is not None and first_y - ROW_GAP - page["top"] >= MIN_ROW_HEIGHT:
            raw[previous_row].append((page_number, (left, page["top"], right, first_y - ROW_GAP)))

        for position, index in enumerate(starts):
            top = rows[index]["y"] - ROW_TOP_PAD
            following = starts[position + 1] if position + 1 < len(starts) else None
            bottom = rows[following]["y"] - ROW_GAP if following is not None else page["bottom"]
            if bottom - top >= MIN_ROW_HEIGHT:
                raw[index].append((page_number, (left, top, right, bottom)))
        previous_row = starts[-1]

    regions: dict[str, list[Region]] = {}
    for index, row in enumerate(rows):
        for page_number, box in raw[index]:
            merged = regions.setdefault(_fold_label(row["label"]), [])
            if merged and merged[-1][0] == page_number and box[1] - merged[-1][1][3] <= MERGE_GAP:
                last = merged[-1][1]
                merged[-1] = (page_number, (last[0], last[1], last[2], max(last[3], box[3])))
            else:
                merged.append((page_number, box))
    return regions
