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

# A leading question number, then every "(xxx)" group after it — a part, a
# sub-part, or a branch's own "(either)"/"(or)" token (see segment_structured.py's
# _ALTERNATIVE_LABELS), in whatever order and however many of them the label
# actually has. Unlike the segmenter's own strict per-level patterns, this
# only ever runs on a label this pipeline already built for itself — never on
# raw text off a page — so there is nothing to gain from also validating that
# a given group looks like a real letter or roman numeral.
#
# The number itself is sometimes a bare "7" and sometimes a section letter
# glued to one — "A1", "B6" — one syllabus's own numbering across its two
# sections, continuing the same count rather than restarting it for the
# second letter (see segment_structured.py's `_LEVEL_PATTERNS`).
_LABEL_NUMBER_RE = re.compile(r"^([A-Z]?\d{1,2})")
_LABEL_GROUP_RE = re.compile(r"\(\s*([a-zA-Z]+)\s*\)")


def _root_ordinal(root: str) -> int:
    """The part of a question root that actually counts up — "7" from "7",
    "1" from "A1", "6" from "B6" — so "A5" then "B6" reads as ascending the
    same way "5" then "6" already does."""
    return int(root.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def normalise_label(label: str) -> str | None:
    """'7 (a) (II)' and '7(a)(ii)' both become '7(a)(ii)'."""
    stripped = label.strip()
    match = _LABEL_NUMBER_RE.match(stripped)
    if not match:
        return None
    out = match.group(1)
    for group in _LABEL_GROUP_RE.finditer(stripped[match.end() :]):
        out += f"({group.group(1).lower()})"
    return out


def _branched_roots(labels: set[str]) -> set[str]:
    """Which question numbers the question paper actually split with an
    "EITHER"/"OR" — i.e. which of its own labels carry an "(either)" or
    "(or)" token anywhere in their chain (see segment_structured.py).

    A mark scheme uses the bare words "EITHER" and "OR" for two different
    things: CAIE's own structural split, and simply offering an alternative
    wording or value for one already-open marking point ("Xe / 131 / 54 OR
    Xe / 131 / 54" — the same nuclide written the other way round). Only a
    question the question paper itself split is ever read as the former;
    everything else is read as content, exactly as if the two words carried
    no special meaning at all.
    """
    roots: set[str] = set()
    for label in labels:
        match = _LABEL_NUMBER_RE.match(label)
        if match is None:
            continue
        groups = {g.group(1).lower() for g in _LABEL_GROUP_RE.finditer(label[match.end() :])}
        if "either" in groups or "or" in groups:
            roots.add(match.group(1))
    return roots


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

# Some subjects' mark schemes have no letter at all — a plain "Question /
# Answer / Mark / Guidance" table whose "Mark" column is just the number on
# its own line, with no B/M/C/A in front of it. On its own that number is
# indistinguishable in shape from a bare question number ("2" is both "2
# marks" and "question 2") — resolved by checking, once per document,
# whether a bare number could ever mean anything other than a question
# number there: a document that gives its marks a letter code, or gives
# them bracketed inline ("sun / light ; [1]", never on a line of its own),
# is read the usual way, and only a document that does neither — where a
# bare number is never anything else — treats one as a mark value instead
# of a question number, since in that style a real question root is always
# glued to the part that opens it
# ("1(a)"), never printed bare and alone.
_BARE_MARK_VALUE_RE = re.compile(r"^\d{1,2}$")

# A mark award folded into a marking point's own line rather than sitting on
# a line of its own — "sun / light ; [1]". Its presence anywhere means a
# bare number never needs to double as one, the same way a lettered code
# elsewhere in the document already would.
_INLINE_MARK_AWARD_RE = re.compile(r"\[\d{1,2}\]")

# A bare "7", or a section-lettered "A1"/"B6" (see `_root_ordinal` above).
_QUESTION_NUMBER_RE = re.compile(r"^[A-Z]?\d{1,2}$")
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

# A third way the "Question / Answer / Marks" table style marks an
# "EITHER"/"OR" split (see segment_structured.py's `_ALTERNATIVE_LABELS`):
# folded into the row's own repeated label instead of a heading row of its
# own — "5E(a)", "7O(b)(i)" — with no separator from the question number at
# all. The lookahead requires whitespace, an opening paren, or the end of the
# line right after the letter, since "the letter E immediately followed by a
# digit" is also standard-form scientific notation ("5E10" for 5×10^10) —
# a shape no genuine label ever takes. Deliberately uppercase-only, unlike
# the other two forms below: a lower-case "5e10" is exactly that notation,
# with nothing else here to tell the two apart.
_GLUED_BRANCH_RE = re.compile(r"^(\d{1,2})([EO])(?=[\s(]|$)")

# The same split, printed the other way round: a label carrying the split's
# own opening word at the *end* of its line instead — "(b) EITHER" — rather
# than the word opening a line of its own.
#
# Matches "EITHER"/"OR" and "Either"/"Or" — CAIE prints one in some subjects
# and the other elsewhere, sometimes both in the same document — but not the
# all lower-case "either"/"or": unlike the other two, that is an ordinary
# word ordinary prose uses constantly, including alone on a wrapped line,
# and nothing else here would tell a real split from a false one at that
# point (see segment_structured.py's own `_ALTERNATIVE_LABELS`).
_TRAILING_BRANCH_RE = re.compile(r"^(.*\S)\s+(EITHER|OR|Either|Or)$")


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

    `path` plays the same role here as it does in segment_structured.py:
    ["8"], then ["8", "a"], then ["8", "a", "i"] — one entry per label from
    the question down to whatever is currently open. A standalone "EITHER" or
    "OR" line (CAIE's own way of offering two ways to answer the same
    question — see segment_structured.py's `_ALTERNATIVE_LABELS`) opens a
    branch with the same "either"/"or" token the segmenter assigns, and a part or
    sub-part token found after it attaches one place deeper than usual for as
    long as the branch stays open — `branch_index` is the depth it occupies.

    Unlike the segmenter, this cannot always place the branch the moment it
    opens: the "Question / Answer / Marks" table style never restates a part
    that was already open before the split (only the older prose style
    does), so the row right after "EITHER" is sometimes the first thing that
    names which part the split actually belongs to. Placing the branch is
    therefore deferred (`pending_branch`) until the next part or sub-part
    label arrives, and that label's own shape then decides which side of it
    the branch goes on: `known_questions` doubles here as every label the
    question paper produced, so "does '8(b)' already exist without the
    split" is answered by a lookup rather than a guess. If it does, the part
    was open before the split and the branch nests inside it ('8(b)(either)');
    if it does not, the split opened before any part did and the part is what
    nests inside the branch instead ('8(either)(a)') — the same two shapes
    segment_structured.py produces from the question paper's own geometry.
    """
    entries: list[MarkSchemeEntry] = []
    path: list[str] = []
    branch_index: int | None = None
    pending_branch: str | None = None
    content: list[str] = []
    marks = 0

    def flush() -> None:
        nonlocal content, marks
        if not path or not content:
            content, marks = [], 0
            return
        label = path[0] + "".join(f"({p})" for p in path[1:])
        entries.append(
            MarkSchemeEntry(display_label=label, marks=marks or None, content=" ".join(content))
        )
        content, marks = [], 0

    content_lines = _structured_content_lines(pages_text)

    # "(b) EITHER" — a part label carrying the split's own opening word on
    # the same line, the same merged-run habit segment_structured.py sees on
    # the question paper's side. Split so the part label is read first and
    # the split opens right after it, same as if they had been printed on
    # two lines to begin with.
    trailing_split: list[str] = []
    for line in content_lines:
        m = _TRAILING_BRANCH_RE.match(line)
        if m is None:
            trailing_split.append(line)
            continue
        trailing_split.append(m.group(1))
        trailing_split.append(m.group(2))
    content_lines = trailing_split

    bare_number_could_be_a_root = any(_MARK_CODE_RE.match(line) for line in content_lines) or any(
        _INLINE_MARK_AWARD_RE.search(line) for line in content_lines
    )

    branched_roots = _branched_roots(known_questions) if known_questions is not None else None
    if branched_roots is not None:
        expanded_lines: list[str] = []
        for line in content_lines:
            glued = _GLUED_BRANCH_RE.match(line)
            if glued is None or glued.group(1) not in branched_roots:
                expanded_lines.append(line)
                continue
            expanded_lines.append(glued.group(1))
            expanded_lines.append("EITHER" if glued.group(2) == "E" else "OR")
            rest = line[glued.end() :].strip()
            if rest:
                expanded_lines.append(rest)
        content_lines = expanded_lines

    for line_index, raw_line in enumerate(content_lines):
        if not raw_line:
            continue
        line = _DOUBLED_OPEN_PAREN_RE.sub(r"(\1)", raw_line)
        line = _PAREN_CLOSE_SPACE_RE.sub(")", _PAREN_INNER_SPACE_RE.sub("(", line))
        line = _GLUED_LABEL_RE.sub(r" \1", line)
        line = _DIGIT_AFTER_PAREN_RE.sub(r" \1", line)

        # "A1" is both a mark code (Accuracy, worth 1 mark) and, in a
        # section-lettered syllabus, a genuine question root — a real,
        # known root always wins the ambiguity, since a mark code is never
        # itself a label the question paper produced.
        is_known_root = known_questions is not None and line in known_questions
        if not is_known_root and (code := _MARK_CODE_RE.match(line)) is not None:
            marks += int(code.group(2))
            continue

        if (
            line in ("EITHER", "OR", "Either", "Or")
            and path
            and (branched_roots is None or path[0] in branched_roots)
        ):
            if line in ("EITHER", "Either"):
                # Where this actually belongs is not yet knowable — see the
                # docstring — so it waits for the part or sub-part label
                # that turns up on a later row instead of guessing now.
                flush()
                pending_branch = "either"
                branch_index = None
                continue
            if branch_index is None:
                # Nothing ever named the part or sub-part "EITHER" opened —
                # its own content is prose only, no further label of its
                # own — so it must be answering whatever was already open,
                # attaching right there. The content already gathered since
                # "EITHER" belongs to that placement, so it is pinned down
                # *before* flushing rather than after, the same immediate
                # placement segment_structured.py always uses when nothing
                # forces a later one.
                branch_index = len(path)
                path = path[:branch_index] + [pending_branch or "either"]
                pending_branch = None
            flush()
            path = path[:branch_index] + ["or"]
            continue

        tokens = line.split()
        i = 0
        new_root: str | None = None
        new_part: str | None = None
        new_sub: str | None = None

        current_root = path[0] if path else None
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
            # is reached and fails its own ascending check in turn. Not
            # required for a section-lettered root ("A1"): a measurement is
            # never written that way, so the shape alone already rules out
            # what this guards against — which matters here, since a
            # section-lettered question sometimes opens with a guidance note
            # of its own before any part does ("A1 Allow correct name but
            # formula takes precedence"), with nothing part-shaped anywhere
            # on the same line. Also not enough on its own in a document
            # where a bare number could only ever be its "Mark" column
            # value — there, a real root is always glued to the part that
            # opens it instead (see `_BARE_MARK_VALUE_RE`).
            and (
                tokens[i][0].isalpha()
                or (i + 1 == len(tokens) and bare_number_could_be_a_root)
                or (
                    i + 1 < len(tokens)
                    and (_PART_TOKEN_RE.match(tokens[i + 1]) or _SUBPART_TOKEN_RE.match(tokens[i + 1]))
                )
            )
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
            if current_root is None or _root_ordinal(tokens[i]) > _root_ordinal(current_root):
                new_root = tokens[i]
                i += 1
            elif tokens[i] == current_root:
                # The "Question / Answer / Marks" table style (roughly 2017
                # on) repeats the current question's own root number on every
                # one of its rows — "1(b)(i)", "1(c)" — not only when a new
                # question opens, unlike the older prose style where a
                # question's number appears exactly once. Consumed here
                # without treating it as a transition, so the part or
                # sub-part token that follows still gets read; a number
                # that is neither ascending nor a repeat of the current one
                # is left as content, the same as before.
                i += 1

        # Same slots segment_structured.py computes for a real part/sub-part
        # marker: normally 1 and 2, each pushed one deeper once a branch has
        # claimed that slot or shallower. Read off the state *before* this
        # row's own root token (if any) changed it, same as the part/sub
        # variables this replaced were never reset until after both checks
        # below ran — harmless, since CAIE's own lettering never opens a
        # fresh question straight into a part called "(i)", "(v)" or "(x)".
        part_index = 1 + (1 if branch_index is not None and branch_index <= 1 else 0)
        sub_index = 2 + (1 if branch_index is not None and branch_index <= 2 else 0)
        current_part = path[part_index] if len(path) > part_index else None
        current_sub = path[sub_index] if len(path) > sub_index else None

        part_match = _PART_TOKEN_RE.match(tokens[i]) if i < len(tokens) else None
        # "(i)", "(v)" and "(x)" are both a single-letter part shape and a
        # roman numeral, and once a part is already open the roman reading
        # is what is actually meant — sub-part (v) is common, a fourth- or
        # fifth part called "(v)" is not something CAIE's own lettering ever
        # reaches. Treated as a part match here, "(v)" would close out the
        # real part (b) mid-way and misfile everything after it under a
        # part that does not exist. Skipping it here (i left unchanged)
        # lets the sub-part check just below try the same token instead.
        if part_match and (
            part_match.group(1).lower() not in ("i", "v", "x") or current_part is None
        ):
            candidate = part_match.group(1).lower()
            # Same table-row repetition as the question number, one level
            # down: "(b)" reprints on every row of part (b), including the
            # ones that are really just its own next enumerated point
            # ("10(b)(ii)2"). Consumed either way so the sub-part token
            # after it still gets read, but only counted as *opening* (b)
            # the first time — repeating it must not re-flush and reopen
            # the entry that same row's own content belongs to.
            if candidate != current_part:
                new_part = candidate
            i += 1
        if i < len(tokens) and (m := _SUBPART_TOKEN_RE.match(tokens[i])):
            candidate = m.group(1).lower()
            if candidate != current_sub:
                new_sub = candidate
            i += 1

        rest = " ".join(tokens[i:])
        consumed = i > 0

        if new_root or new_part or new_sub:
            flush()
            if new_root:
                path = [new_root]
                branch_index = None
                pending_branch = None
            if new_part:
                # A part label is the first thing that can name where a split
                # pending since the last "EITHER"/"OR" actually belongs — see
                # the docstring. Once placed it behaves exactly like a branch
                # the segmenter placed immediately: unresolved (a sub-part
                # naming it first instead, not seen on any real paper yet) is
                # left for a future paper to force a fix.
                if pending_branch is not None and branch_index is None:
                    root = path[0] if path else new_root
                    if known_questions is not None and f"{root}({new_part})" in known_questions:
                        path = path[:1] + [new_part, pending_branch]
                        branch_index = 2
                    else:
                        path = path[:1] + [pending_branch, new_part]
                        branch_index = 1
                    pending_branch = None
                else:
                    idx = 1 + (1 if branch_index is not None and branch_index <= 1 else 0)
                    path = path[:idx] + [new_part]
            if new_sub:
                idx = 2 + (1 if branch_index is not None and branch_index <= 2 else 0)
                path = path[:idx] + [new_sub]
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
        elif not bare_number_could_be_a_root and _BARE_MARK_VALUE_RE.match(line):
            marks += int(line)
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
