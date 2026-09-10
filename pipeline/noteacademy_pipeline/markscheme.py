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


_STRUCTURED_HEADER_END = re.compile(r"^©")

# The "Question / Answer / Marks" table style repeats its own column header
# on every page, right after the copyright line and a "Page N of M" footer —
# boilerplate exactly like the rest of the header, but past where the single
# `_STRUCTURED_HEADER_END` line was assumed to end it. Left in, this becomes
# three extra content lines tacked onto whatever entry was still open when
# the page broke: real, already-matched entries picked up "Question Answer
# Marks" at the end of their own content on every paper using this style,
# not only the ones a page break happened to land on awkwardly.
_PAGE_FOOTER_RE = re.compile(r"^Page \d+ of \d+$")
_TABLE_HEADER_WORDS = {"Question", "Answer", "Marks"}

# The same "Section B" heading the question paper carries between its own
# sections (see segment_structured.py's SECTION_RE) is reprinted in the mark
# scheme too, wherever the last question of one section falls at a table
# break — page furniture, not a marking point for whatever question was open.
_SECTION_HEADING_RE = re.compile(r"^Section [A-Z]$")

# A CAIE structured mark scheme's own mark-type codes: B (independent mark),
# M (method mark), C (method mark contingent on an earlier one), A (accuracy
# mark, contingent on its C). Always a letter immediately followed by the
# number of marks it is worth, alone on its own line.
_MARK_CODE_RE = re.compile(r"^([BMCA])(\d)$", re.IGNORECASE)

_QUESTION_NUMBER_RE = re.compile(r"^\d{1,2}$")
#  Case-sensitive on purpose, unlike the segmenter's own version of these
# patterns: CAIE always prints a real part or sub-part label in lowercase,
# but a mark scheme's own marking-point text routinely names a labelled
# point on a diagram the same way — "(Z) has the same potential
# difference", where Z is a component in the circuit, not a part. Matching
# either case here read that as part "(z)" opening, closing out whatever
# part was actually open and misfiling every point after it under a part
# that does not exist.
_PART_TOKEN_RE = re.compile(r"^\(([a-z])\)$")
_SUBPART_TOKEN_RE = re.compile(r"^\((i|ii|iii|iv|v|vi|vii|viii|ix|x)\)$")

# CAIE's three-column "Question / Answer / Marks" mark-scheme table (used from
# roughly 2017 on) prints a label as one glued run — "1(a)", "1(b)(i)" — with
# no space at all, unlike the older prose-style scheme's "10 (a)" or
# "(a) (i)". The token-by-token walk below only ever recognises one label
# component per token, so a glued run matched none of them and was silently
# read as body text — every label in the paper, not just the merged ones,
# which is why this failed completely rather than losing a few labels the
# way the space-separated merge case does. Splitting on a paren that follows
# a digit or a closing paren turns "1(b)(i)" into "1 (b) (i)" before it ever
# reaches the tokeniser; a paren glued to a letter ("resultant(net) force")
# has neither preceding it and is left alone.
_GLUED_LABEL_RE = re.compile(r"(?<=[0-9)])(\([a-z]+\))", re.IGNORECASE)

# The same table glues a marking point's own enumerated list straight onto
# the sub-part label that opens it — "10(b)(ii)1" is sub-part (ii)'s first
# point, not a fourth label component — so a bare digit immediately after a
# closing paren is split off the same way, leaving "(ii)" recognisable on
# its own and "1" to fall through as content exactly like an unglued
# enumerated point already does.
_DIGIT_AFTER_PAREN_RE = re.compile(r"(?<=\))(\d)")

# A PDF's text layer occasionally puts a stray space just inside a label's
# own parentheses — "(i )" for "(i)" — a rendering artefact of the exact
# glyph spacing in that particular print run, not a real character. Left in,
# it fails the exact match _SUBPART_TOKEN_RE requires, for exactly the same
# reason a glued or mis-cased label does: the token on the page and the
# pattern the label is recognised by disagree on what whitespace means here,
# and it is safe to close the gap because it never appears inside a real
# marking point's own prose, only immediately against a paren.
_PAREN_INNER_SPACE_RE = re.compile(r"\(\s+")
_PAREN_CLOSE_SPACE_RE = re.compile(r"\s+\)")

# Another PDF-specific glitch of the same kind: "1(a)((i)" for "1(a)(i)", an
# extra opening paren stuck to the front of a label with nothing between
# them. Narrow on purpose — it only fires on two opens with no space or text
# between them — so a real nested aside like "(the force (approx))" is
# untouched; its inner paren always has real words before it.
_DOUBLED_OPEN_PAREN_RE = re.compile(r"\(\(([a-z]+)\)")

# Nuclide notation's mass number renders on its own line, with the atomic
# number and element symbol glued together immediately below it —
# "9" then "4Be" for beryllium-9. A bare mass number that happens to equal a
# later, real question number satisfies every other check a genuine
# question start has (ascending, known, alone on its own line), so this is
# what actually tells them apart: nothing else that stands alone on a line
# looks like a digit run glued straight onto an element symbol.
_NUCLIDE_CONTINUATION_RE = re.compile(r"^\d{1,3}[A-Z][a-z]?$")


def _structured_content_lines(pages_text: list[str]) -> list[str]:
    """Every real line of a structured mark scheme, across every page, with
    the repeated page header/footer (page number, syllabus, paper code,
    copyright line — identical on every page) removed.

    The "Question / Answer / Marks" table style also carries one or two
    generic-marking-principles pages before the real per-question table
    starts — "Science-Specific Marking Principles" opens with its own bare
    numbered list, 1 upwards, which is indistinguishable from a real
    question number by shape alone and satisfies the ascending-order and
    known-questions checks just as well as a genuine one. Rather than
    special-case a preamble heading, whose wording is not the same across
    subjects, everything before the first real table header is dropped
    outright: that header is the one thing every version of this style
    reliably prints exactly once before question 1's own row, unlike a
    subject-specific heading that might not be there at all.
    """
    all_lines: list[str] = []
    for text in pages_text:
        page_lines = [line.strip() for line in text.splitlines()]
        header_end = next(
            (i for i, line in enumerate(page_lines) if _STRUCTURED_HEADER_END.match(line)),
            None,
        )
        if header_end is None:
            continue
        all_lines.extend(page_lines[header_end + 1 :])

    table_start = next(
        (i for i, line in enumerate(all_lines) if line == "Question"), 0
    )
    return [
        line
        for line in all_lines[table_start:]
        if line not in _TABLE_HEADER_WORDS
        and not _PAGE_FOOTER_RE.match(line)
        and not _SECTION_HEADING_RE.match(line)
    ]


def _next_content_line(lines: list[str], index: int) -> str | None:
    """The next non-blank line after `index`, or None at the end."""
    for line in lines[index + 1 :]:
        if line:
            return line
    return None


def parse_structured_mark_scheme(
    pages_text: list[str], known_questions: set[str] | None = None
) -> list[MarkSchemeEntry]:
    """Read question-by-question marking points from a structured paper's
    mark scheme.

    CAIE sets this as plain, regular prose rather than a table: a question
    number alone on its own line, then each part and sub-part's label
    immediately followed by its marking point, then that point's mark code
    (B1, C1, A1, M1) alone on the next line. A sub-part can carry more than
    one marking point — its label is not repeated, so a bare content line
    with no label of its own is a second (or third) point for whatever label
    most recently opened, not a new entry.

    Deliberately does not require a question number to open every entry: a
    part with sub-parts is rarely followed by one on the mark scheme's own
    reading, but a fresh top-level number always is, so the two together are
    enough to track which question is current without asking every line to
    repeat it.

    `known_questions` is the question paper's own top-level labels ('1'
    through however many it has). A marking point is itself sometimes an
    enumerated list — "1 speed and direction ... 2 direction changes ..." as
    the reasons behind one mark — and a bare "2" there is indistinguishable
    from a real question number by shape alone. Real question numbers only
    increase across a paper and are drawn from a small, known set; an
    enumerated reason is neither, so both conditions have to hold before a
    bare digit is trusted as a new question rather than folded into the
    current one's content. Without `known_questions` only the ordering is
    checked, which is weaker but still rejects the common case (a list
    restarting from 1 or 2 while well into a later question).

    Also does not assume a label is alone on its own line: the same run-
    together labelling the question paper does — "10 (a) " as one span when a
    question's first part opens on its very first line — happens here too,
    so a line is walked token by token from the left rather than matched as
    one shape, and whatever is left over after the labels found becomes this
    line's own content.
    """
    entries: list[MarkSchemeEntry] = []
    question = part = sub = None
    content: list[str] = []
    marks = 0

    def flush() -> None:
        nonlocal content, marks
        if question is None or not content:
            content, marks = [], 0
            return
        label = question
        if part:
            label += f"({part})"
        if sub:
            label += f"({sub})"
        entries.append(
            MarkSchemeEntry(display_label=label, marks=marks or None, content=" ".join(content))
        )
        content, marks = [], 0

    content_lines = _structured_content_lines(pages_text)
    for line_index, raw_line in enumerate(content_lines):
        if not raw_line:
            continue
        line = _DOUBLED_OPEN_PAREN_RE.sub(r"(\1)", raw_line)
        line = _PAREN_CLOSE_SPACE_RE.sub(")", _PAREN_INNER_SPACE_RE.sub("(", line))
        line = _GLUED_LABEL_RE.sub(r" \1", line)
        line = _DIGIT_AFTER_PAREN_RE.sub(r" \1", line)

        if (code := _MARK_CODE_RE.match(line)) is not None:
            marks += int(code.group(2))
            continue

        tokens = line.split()
        i = 0
        new_question: str | None = None
        new_part: str | None = None
        new_sub: str | None = None

        if (
            i < len(tokens)
            and _QUESTION_NUMBER_RE.match(tokens[i])
            and (known_questions is None or tokens[i] in known_questions)
            # CAIE prints a question number alone on its own line, or merged
            # with the part label that opens it ("10 (a)") — never followed
            # by anything else. Without this, a space-grouped thousands value
            # ("11 250 J") that happens to share a real question's number is
            # indistinguishable from that question actually starting here,
            # and the false start corrupts every label until the real one
            # is reached and fails its own ascending check in turn.
            and (i + 1 == len(tokens) or _PART_TOKEN_RE.match(tokens[i + 1])
                 or _SUBPART_TOKEN_RE.match(tokens[i + 1]))
            # A number alone on its own line is also exactly the shape of a
            # nuclide's mass number, printed with its atomic number and
            # element symbol glued together on the line right after it —
            # "9" then "4Be". Checked only when nothing else on this line
            # already ruled it out, since the next line is otherwise
            # irrelevant to whether this one opens a question.
            and not (
                i + 1 == len(tokens)
                and (next_line := _next_content_line(content_lines, line_index)) is not None
                and _NUCLIDE_CONTINUATION_RE.match(next_line)
            )
        ):
            if question is None or int(tokens[i]) > int(question):
                new_question = tokens[i]
                i += 1
            elif tokens[i] == question:
                # The "Question / Answer / Marks" table style (roughly 2017
                # on) repeats the current question's own number on every one
                # of its rows — "1(b)(i)", "1(c)" — not only when a new
                # question opens, unlike the older prose style where a
                # question's number appears exactly once. Consumed here
                # without treating it as a transition, so the part or
                # sub-part token that follows still gets read; a number
                # that is neither ascending nor a repeat of the current one
                # is left as content, the same as before.
                i += 1
        part_match = _PART_TOKEN_RE.match(tokens[i]) if i < len(tokens) else None
        # "(i)", "(v)" and "(x)" are both a single-letter part shape and a
        # roman numeral, and once a part is already open the roman reading
        # is what is actually meant — sub-part (v) is common, a fourth- or
        # fifth part called "(v)" is not something CAIE's own lettering ever
        # reaches. Treated as a part match here, "(v)" would close out the
        # real part (b) mid-way and misfile everything after it under a
        # part that does not exist. Skipping it here (i left unchanged)
        # lets the sub-part check just below try the same token instead.
        if part_match and (part_match.group(1).lower() not in ("i", "v", "x") or part is None):
            candidate = part_match.group(1).lower()
            # Same table-row repetition as the question number, one level
            # down: "(b)" reprints on every row of part (b), including the
            # ones that are really just its own next enumerated point
            # ("10(b)(ii)2"). Consumed either way so the sub-part token
            # after it still gets read, but only counted as *opening* (b)
            # the first time — repeating it must not re-flush and reopen
            # the entry that same row's own content belongs to.
            if candidate != part:
                new_part = candidate
            i += 1
        if i < len(tokens) and (m := _SUBPART_TOKEN_RE.match(tokens[i])):
            candidate = m.group(1).lower()
            if candidate != sub:
                new_sub = candidate
            i += 1

        rest = " ".join(tokens[i:])
        consumed = i > 0

        if new_question or new_part or new_sub:
            flush()
            if new_question:
                question, part, sub = new_question, None, None
            if new_part:
                part, sub = new_part, None
            if new_sub:
                sub = new_sub
            if rest:
                content.append(rest)
        elif consumed:
            # Every label token on this row repeated what was already open —
            # not a transition, just this row's own share of the same
            # sub-part's content (its next enumerated marking point, most
            # often). Appending `line` here, as the no-label case below
            # does, would put the now-redundant repeated label text back
            # into the content it was just stripped out of.
            if rest:
                content.append(rest)
        else:
            content.append(line)

    flush()
    return entries


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

# CAIE has used two multiple-choice mark scheme layouts. Both were validated
# against real papers (5054/11 May/June 2015, 2019 and 2026):
#
#   modern (2019-)   a single "Question | Answer | Marks" table, one row per line
#                    (or "Mark" — CAIE spells it both ways, sometimes within
#                    one session)
#   legacy (-2015)   "Question Number | Key" in TWO side-by-side columns, no
#                    marks column, so reading order interleaves them:
#                    1 B 21 D / 2 A 22 C / ...
#
# They are parsed by separate functions selected on the header, rather than by
# one permissive parser that tries to cope with both. A loose parser that
# accepts any number-then-letter pair would also happily read a syllabus code
# or a page number, and this is the one artefact where a wrong value is worst:
# a student trusts an answer key completely.
# `marks?` because CAIE's own typesetting is not consistent: Chemistry 5070/11
# and 5070/12 from the same October/November 2019 session head the column
# "Marks" and "Mark" respectively. One character, and the strict parser refused
# the second paper entirely — 40 questions loaded with no answers, flagged for a
# reviewer who could only have re-typed the key by hand. Widening to the
# singular costs nothing: the header is still matched in full, so a page number
# or syllabus code still cannot be mistaken for a table.
MODERN_HEADER = re.compile(r"question\s+answer\s+marks?", re.IGNORECASE)
LEGACY_HEADER = re.compile(r"question\s+number\s+key", re.IGNORECASE)
VALID_OPTIONS = frozenset("ABCDE")


def _tokens(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _is_option(token: str) -> bool:
    return len(token) == 1 and token.upper() in VALID_OPTIONS


def _parse_modern_grid(text: str) -> list[tuple[str, str]]:
    """Rows of [number][option][marks].

    Requiring the marks column is what keeps page furniture out: "Page 2 of 3"
    and "5054/11 Mark Scheme June 2026" both contain bare numbers, but neither
    forms a triple.
    """
    pairs: list[tuple[str, str]] = []
    tokens = _tokens(text)
    i = 0
    while i + 2 < len(tokens):
        number, option, marks = tokens[i], tokens[i + 1], tokens[i + 2]
        if number.isdigit() and _is_option(option) and marks.isdigit():
            pairs.append((str(int(number)), option.upper()))
            i += 3
        else:
            i += 1
    return pairs


def _parse_legacy_grid(text: str) -> list[tuple[str, str]]:
    """Rows of [number][option], from a two-column table with no marks column.

    Reading order interleaves the columns (1 B 21 D / 2 A 22 C), which is
    harmless here: every pair is recorded and the caller checks the set is
    complete and contiguous.

    Only text *after* the header is considered, so the cover page's syllabus
    code and "maximum raw mark 40" cannot contribute a pair.
    """
    match = LEGACY_HEADER.search(re.sub(r"[ \t]+", " ", text))
    if not match:
        return []

    pairs: list[tuple[str, str]] = []
    tokens = _tokens(text)

    # Skip past the header tokens themselves.
    start = 0
    for index, token in enumerate(tokens):
        if token.lower() == "key":
            start = index + 1
            break

    i = start
    while i + 1 < len(tokens):
        number, option = tokens[i], tokens[i + 1]
        if number.isdigit() and _is_option(option):
            pairs.append((str(int(number)), option.upper()))
            i += 2
        else:
            i += 1
    return pairs


def parse_mcq_answer_grid(page_texts: list[str]) -> dict[str, str]:
    """Read the answer key out of an MCQ mark scheme's text layer.

    Accepts the text of every page and returns {question number: option}.
    Returns an empty dict when no page looks like an answer grid — a scanned
    mark scheme, or a structured paper — so the caller falls back to vision.
    """
    answers: dict[str, str] = {}
    conflicts: set[str] = set()

    for text in page_texts:
        flattened = re.sub(r"\s+", " ", text)

        if MODERN_HEADER.search(flattened):
            pairs = _parse_modern_grid(text)
        elif LEGACY_HEADER.search(flattened):
            pairs = _parse_legacy_grid(text)
        else:
            continue

        for key, value in pairs:
            if key in answers and answers[key] != value:
                # The same question answered twice, differently. Never pick
                # one; drop it and let a human look.
                conflicts.add(key)
            answers[key] = value

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
