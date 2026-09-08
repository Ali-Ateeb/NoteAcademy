"""Match mark-scheme entries to the questions they mark.

For MCQ papers this is exact: the mark scheme is an answer grid keyed by question
number, so matching is a dictionary lookup and cannot go wrong.

For structured papers the mark scheme uses its own numbering, which *usually*
mirrors the question paper and sometimes does not — merged parts ('3(b)(i) and
(ii)'), renumbering, or an entry covering a range. Matching is therefore
deliberately conservative: exact matches are accepted, everything else is
reported as unmatched. An unmatched question is a question a human looks at; a
question silently attached to the wrong mark scheme is a question that teaches a
student the wrong thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import MarkSchemeEntry

# Splits '7(a)(ii)' into its parts, tolerating the spacing variations CAIE uses.
LABEL_PARTS = re.compile(
    r"(?P<number>\d{1,2})\s*"
    r"(?:\(\s*(?P<part>[a-z])\s*\))?\s*"
    r"(?:\(\s*(?P<sub>i{1,3}|iv|v|vi{1,3}|ix|x)\s*\))?",
    re.IGNORECASE,
)


def normalise_label(label: str) -> str | None:
    """'7 (a) (II)' and '7(a)(ii)' both become '7(a)(ii)'."""
    match = LABEL_PARTS.match(label.strip())
    if not match or not match.group("number"):
        return None
    out = match.group("number")
    if match.group("part"):
        out += f"({match.group('part').lower()})"
    if match.group("sub"):
        out += f"({match.group('sub').lower()})"
    return out


@dataclass
class MatchResult:
    matched: dict[str, MarkSchemeEntry] = field(default_factory=dict)
    unmatched_questions: list[str] = field(default_factory=list)
    unmatched_entries: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.unmatched_questions and not self.unmatched_entries

    def summary(self) -> str:
        return (
            f"{len(self.matched)} matched, "
            f"{len(self.unmatched_questions)} questions without a mark scheme, "
            f"{len(self.unmatched_entries)} mark scheme entries without a question"
        )


def match_mark_scheme(
    question_labels: list[str], entries: list[MarkSchemeEntry]
) -> MatchResult:
    """Pair questions with mark-scheme entries by normalised label.

    Exact matching only. The temptation is to fall back to fuzzy matching for the
    leftovers, which raises the match rate and lowers the accuracy — precisely
    the wrong trade for a revision tool. Leftovers go to review.
    """
    result = MatchResult()

    by_label: dict[str, MarkSchemeEntry] = {}
    ambiguous: set[str] = set()

    for entry in entries:
        key = normalise_label(entry.display_label)
        if key is None:
            result.unmatched_entries.append(entry.display_label)
            continue
        if key in by_label or key in ambiguous:
            # Two entries claiming the same question: the paper uses a shape this
            # parser does not model. Withdraw *both* — keeping the first and
            # discarding the second would just be picking one at random.
            ambiguous.add(key)
            by_label.pop(key, None)
            continue
        by_label[key] = entry

    result.unmatched_entries.extend(sorted(ambiguous))

    for label in question_labels:
        key = normalise_label(label)
        if key is not None and key in by_label:
            result.matched[label] = by_label.pop(key)
        else:
            result.unmatched_questions.append(label)

    result.unmatched_entries.extend(by_label.keys())
    return result


def match_mcq_answers(
    question_labels: list[str], answers: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Attach answer-grid letters to MCQ questions. Exact, by question number."""
    matched: dict[str, str] = {}
    unmatched: list[str] = []

    normalised = {normalise_label(k) or k: v for k, v in answers.items()}
    for label in question_labels:
        key = normalise_label(label)
        if key is not None and key in normalised:
            matched[label] = normalised[key]
        else:
            unmatched.append(label)

    return matched, unmatched


# ---------------------------------------------------------------------------
# MCQ answer grids
#
# Validated against real CAIE papers (5054/11 May/June 2026): the multiple-choice
# mark scheme is a "Question / Answer / Marks" table that survives intact in the
# PDF's text layer. It needs no vision model at all — this is a strict parser
# over the text, which means zero inference cost and, more importantly, zero
# possibility of a hallucinated answer key.
#
# An answer key is the one artefact where a plausible-looking error is worst: a
# student trusts it completely and learns the wrong thing. So the parser refuses
# anything it does not recognise rather than guessing, and the caller falls back
# to vision only when this returns nothing.
# ---------------------------------------------------------------------------

GRID_HEADER = re.compile(r"question\s+answer\s+marks", re.IGNORECASE)
VALID_OPTIONS = frozenset("ABCDE")


def parse_mcq_answer_grid(page_texts: list[str]) -> dict[str, str]:
    """Read the answer key out of an MCQ mark scheme's text layer.

    Accepts the text of every page and returns {question number: option}.
    Returns an empty dict when the pages do not look like an answer grid — a
    scanned mark scheme, or a structured paper — so the caller can fall back to
    the vision path.

    Rows are matched as a strict [number][single letter][mark count] triple.
    Requiring the marks column is what keeps page furniture out: "Page 2 of 3"
    and "5054/11 Mark Scheme June 2026" both contain bare numbers, but neither
    forms a triple.
    """
    answers: dict[str, str] = {}
    conflicts: set[str] = set()

    for text in page_texts:
        if not GRID_HEADER.search(re.sub(r"\s+", " ", text)):
            continue

        tokens = [line.strip() for line in text.splitlines() if line.strip()]
        i = 0
        while i + 2 < len(tokens):
            number, option, marks = tokens[i], tokens[i + 1], tokens[i + 2]
            if (
                number.isdigit()
                and len(option) == 1
                and option.upper() in VALID_OPTIONS
                and marks.isdigit()
            ):
                key = str(int(number))
                value = option.upper()
                if key in answers and answers[key] != value:
                    # The same question answered twice, differently. Never pick
                    # one; drop it and let a human look.
                    conflicts.add(key)
                answers[key] = value
                i += 3
            else:
                i += 1

    for key in conflicts:
        answers.pop(key, None)

    return answers


def validate_answer_grid(answers: dict[str, str], expected_count: int) -> list[str]:
    """Check a parsed grid is complete and contiguous before it is trusted.

    A grid that is missing question 27, or that stops at 38 of 40, is a parser
    failure — not a paper with fewer questions. Reporting that is the difference
    between noticing and shipping a bank with holes in it.
    """
    problems: list[str] = []

    if len(answers) != expected_count:
        problems.append(f"parsed {len(answers)} answers, expected {expected_count}")

    numbers = sorted(int(k) for k in answers)
    if numbers:
        missing = sorted(set(range(1, expected_count + 1)) - set(numbers))
        if missing:
            problems.append(f"missing question(s): {missing}")
        if numbers[0] != 1:
            problems.append(f"grid starts at {numbers[0]}, not 1")

    return problems
