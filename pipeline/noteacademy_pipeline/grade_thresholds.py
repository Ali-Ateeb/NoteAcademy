"""Parse a Cambridge grade-threshold PDF.

Cambridge publishes two tables in every one of these PDFs: thresholds per
individual component (grades A-E, no A*), and thresholds per *combination* of
components (grades A*-E) -- a candidate's actual overall grade depends on
which combination of components they sat, and a subject publishes several
(physics 5054 has four: AX, AY, BX, BY; chemistry 5070 has six). Both tables
publish only each grade's *minimum* mark; a grade's range runs up to one
below the next grade up's minimum, and the top grade runs up to the paper's
own maximum mark.

The parse is position-based, not label-based: PyMuPDF's text extraction puts
every cell of these tables on its own line (confirmed against real PDFs, not
assumed), so a component or combination row is recognised by its own label
line, then read as a fixed run of the numeric lines that follow it.

Cambridge changed the combination table's layout starting with the June 2026
series (found by actually parsing all nine currently-published series, not
assumed from one sample): the two-letter option code (`AX`, `BY`, ...) is
gone, and a row is now keyed directly by its own component list (`11, 21,
41`). Both layouts are handled -- the combination identifier is the option
code where Cambridge still prints one, and the component list itself where
it does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

COMPONENT_LINE = re.compile(r"^Component (\d+)$")
COMBINATION_CODE_LINE = re.compile(r"^([A-Z]{2})$")
COMPONENT_LIST_LINE = re.compile(r"^\d{1,2}(?:,\s*\d{1,2})*$")
GRADES_COMPONENT = ["A", "B", "C", "D", "E"]
GRADES_COMBINATION = ["A*", "A", "B", "C", "D", "E"]


@dataclass(frozen=True)
class ThresholdRow:
    grade: str
    min_mark: int
    max_mark: int


@dataclass(frozen=True)
class ComponentThresholds:
    component: int
    variant: int | None
    rows: list[ThresholdRow]


@dataclass(frozen=True)
class CombinationThresholds:
    combination: str
    components: str  # e.g. '11, 21, 31', exactly as Cambridge printed it
    rows: list[ThresholdRow]


def _cascade(max_available: int, grades: list[str], min_marks: list[int]) -> list[ThresholdRow]:
    rows = []
    ceiling = max_available
    for grade, min_mark in zip(grades, min_marks, strict=True):
        rows.append(ThresholdRow(grade, min_mark, ceiling))
        ceiling = min_mark - 1
    return rows


def parse_grade_threshold_pdf(
    path: Path,
) -> tuple[list[ComponentThresholds], list[CombinationThresholds]]:
    with pymupdf.open(path) as doc:
        text = "\n".join(page.get_text("text") for page in doc)
    return parse_grade_threshold_text(text, source=path)


def parse_grade_threshold_text(
    text: str, *, source: Path | str = "<text>"
) -> tuple[list[ComponentThresholds], list[CombinationThresholds]]:
    """The actual parse, independent of PDF extraction so it can be tested
    directly against known-good text. Raises ValueError if the text does not
    match the expected layout -- refused rather than guessed at, the same
    policy `naming.py` states for a filename that does not parse."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    components: list[ComponentThresholds] = []
    combinations: list[CombinationThresholds] = []
    in_combinations = False

    i = 0
    while i < len(lines):
        line = lines[i]

        if not in_combinations:
            # 'A*' never appears in the per-component table (Cambridge is
            # explicit that A* does not exist at component level), so its
            # first appearance anywhere is the combination table's own
            # header -- true regardless of which wording introduces it.
            if line == "A*":
                in_combinations = True
                i += 1
                continue

            match = COMPONENT_LINE.match(line)
            if match:
                values = [int(v) for v in lines[i + 1 : i + 7]]
                max_available, *min_marks = values
                # Cambridge's "Component 11" prints a component and variant
                # together, the same pair `papers.component`/`papers.variant`
                # store separately -- same decomposition `naming.py` uses for
                # a CAIE filename's own paper number.
                code = match.group(1)
                component, variant = int(code[0]), (int(code[1]) if len(code) == 2 else None)
                components.append(
                    ComponentThresholds(
                        component, variant,
                        _cascade(max_available, GRADES_COMPONENT, min_marks),
                    )
                )
                i += 7
                continue
        else:
            code_match = COMBINATION_CODE_LINE.match(line)
            if code_match:
                max_after_weighting = int(lines[i + 1])
                component_list = lines[i + 2]
                min_marks = [int(v) for v in lines[i + 3 : i + 9]]
                combinations.append(
                    CombinationThresholds(
                        code_match.group(1),
                        component_list,
                        _cascade(max_after_weighting, GRADES_COMBINATION, min_marks),
                    )
                )
                i += 9
                continue

            list_match = COMPONENT_LIST_LINE.match(line)
            if list_match:
                component_list = line
                max_after_weighting = int(lines[i + 1])
                min_marks = [int(v) for v in lines[i + 2 : i + 8]]
                combinations.append(
                    CombinationThresholds(
                        component_list.replace(" ", ""),
                        component_list,
                        _cascade(max_after_weighting, GRADES_COMBINATION, min_marks),
                    )
                )
                i += 8
                continue
        i += 1

    if not components or not combinations:
        raise ValueError(f"{source}: did not find the expected threshold tables")
    return components, combinations
