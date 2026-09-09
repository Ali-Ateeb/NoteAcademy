"""Read a CAIE syllabus PDF into a topic tree.

The topic tree is the spine of the whole product. Tagging picks from it as a
closed list, the topical browser is a view of it, and `topic_links` maps a
student on one syllabus version onto questions written for another. Transcribing
it by hand is a day of work per subject and introduces exactly the errors that
are hardest to notice — a missing outcome is invisible, and a mistyped code
silently detaches every question tagged with it.

So it is parsed, and parsed *strictly*. CAIE typesets these documents from a
single template, and the template is stated in geometry rather than inferred:

    section          13pt bold      '1  Motion, forces and energy'
    topic            10pt bold      '1.2  Motion'
    subtopic         10pt regular   '1.7.3  Energy resources'
    objective        10pt regular   number in a gutter at x=62, text at x=85
    denominator                     the line under a drawn fraction bar

That last rule is the one worth explaining. Equations are set as fractions, and
a fraction extracts as two lines: 'speed = distance' and, below it, 'time'.
Joined by the newline that becomes a space, it reads 'speed = distance time' —
not a slightly worse rendering of the equation but a different and false one.

The fraction *bar* is drawn on the page, so it is the signal used rather than
indentation: the line above a bar is the numerator, the line below it the
denominator, and they are joined with ' / '. Every bar is accounted for, and a
bar the parser cannot resolve is reported rather than silently flattened.

Reading order needs the same treatment. A fraction's three parts —
'specific heat capacity =', 'change in energy', 'mass × change in temperature' —
sit at three different heights, so sorting by vertical position alone puts the
numerator before the words that introduce it. Lines that overlap vertically are
therefore one visual row, read left to right, which is how a person reads them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# The template's two left edges: the number gutter, and the text column.
GUTTER_X = 62.4
BODY_X = 85.0
GUTTER_MAX_X = 75.0

# Running heads and folios, excluded from the body.
BODY_TOP = 40.0
BODY_BOTTOM = 800.0

# A real line's height is close to its font size (12.15pt on 10pt body text,
# measured across this template) — but at least one glyph, the reversible-
# reaction arrow (⇌, U+21CC), reports a broken ascent/descent in the fonts
# both the chemistry and biology syllabuses embed, and pymupdf's line bbox is
# the union of every glyph in it: one arrow inflates the whole line's height
# to five times normal and swallows several real lines beneath it into a
# single bogus row — "6.3 Reversible reactions and equilibrium" absorbed the
# next two numbered objectives this way. Clamped back to a plausible single
# line's height rather than trusted; nothing about a normal line comes near
# this ratio, so the clamp is inert everywhere else.
MAX_LINE_HEIGHT_RATIO = 2.0

# Lines sharing a baseline within this many points are one row: a number and the
# text beside it, or a section number and its title.
ROW_TOLERANCE = 2.0

# Lines overlapping vertically by at least this fraction of the shorter one are
# one visual row: the three parts of a fraction, read left to right.
ROW_OVERLAP = 0.25

# A fraction bar is a short horizontal rule. Anything wider is a table border.
BAR_MAX_HEIGHT = 2.5
BAR_MIN_WIDTH = 5.0
BAR_MAX_WIDTH = 250.0

# A typeset equation is inline: a page states one, or a small few, and each
# sits alone. A page with more candidates than this is not printing equations
# — it is a table (a bottom rule split into one segment per column, as many
# times as the table has rows) or a structural diagram (a bond drawn as a
# line, one such "bar" per bond). Both happen to fall inside CAIE's own width
# and height bands for a fraction bar, and pairing them guesses at a
# numerator/denominator relationship that specific chemistry and biology
# syllabuses have shown does not exist — silently, since a wrong pairing does
# not raise `unresolved_bars` the way a missing line does. A page over this
# count is treated as having no fraction bars at all: its body text still
# reads correctly, just without folding anything into "x / y".
MAX_BARS_PER_PAGE = 3

SECTION_SIZE = 12.0          # section headings are 13pt; topics and body are 10
CHAPTER_SIZE = 16.0          # '3 Subject content' and its siblings are 18pt

SECTION = re.compile(r"^(\d+)\s+(.+)$")
TOPIC = re.compile(r"^(\d+\.\d+)\s+(.+)$")
SUBTOPIC = re.compile(r"^(\d+\.\d+\.\d+)\s+(.+)$")
OBJECTIVE = re.compile(r"^(\d+)\s+(.+)$")

# CAIE repeats a heading with this suffix when a topic runs over a page break.
CONTINUED = re.compile(r"\s+continued\s*$", re.IGNORECASE)


@dataclass
class Topic:
    code: str
    title: str
    slug: str
    learning_objectives: list[str] = field(default_factory=list)
    children: list[Topic] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return self.code.count(".")


@dataclass
class Syllabus:
    subject_title: str = ""
    syllabus_code: str = ""
    label: str = ""
    first_exam_year: int | None = None
    last_exam_year: int | None = None
    sections: list[Topic] = field(default_factory=list)
    fraction_bars: int = 0
    unresolved_bars: int = 0
    problems: list[str] = field(default_factory=list)

    def walk(self):
        """Every topic, parents before children, in document order."""
        def visit(topic: Topic):
            yield topic
            for child in topic.children:
                yield from visit(child)

        for section in self.sections:
            yield from visit(section)

    @property
    def objective_count(self) -> int:
        return sum(len(topic.learning_objectives) for topic in self.walk())


def slugify(title: str) -> str:
    """A URL segment for a topic title.

    Accents are folded rather than dropped: 'Réfraction' becomes 'refraction',
    not 'rfraction', which matters the moment this is pointed at a subject whose
    titles are not plain ASCII.
    """
    folded = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    return slug or "topic"


def clean(text: str) -> str:
    """Collapse the typesetting whitespace CAIE's PDFs are full of."""
    # Tabs, en spaces, and the private-use bullet the template uses.
    text = text.replace("\t", " ").replace(" ", " ").replace("\x07", "")
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Line:
    """One extracted text line, before anything is decided about it."""

    text: str
    x0: float
    y0: float
    y1: float
    x1: float
    size: float
    bold: bool
    is_denominator: bool = False
    denominator: Line | None = None

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Row:
    """One visual row: every line that overlaps it vertically, read left to right.

    A row is what a person would call a line of the page. A fraction occupies
    one row and three text lines, and reading them in x order is what turns
    'change in energy / specific heat capacity = / mass × change in temperature'
    back into the equation as printed.
    """

    text: str
    x0: float
    y0: float
    y1: float
    size: float
    bold: bool

    @property
    def in_gutter(self) -> bool:
        return self.x0 < GUTTER_MAX_X


def page_lines(page: pymupdf.Page) -> list[Line]:
    lines: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            if not text.strip():
                continue
            x0, y0, x1, y1 = line["bbox"]
            if not (BODY_TOP < y0 < BODY_BOTTOM):
                continue
            span = line["spans"][0]

            # line["bbox"] is the union of every span's own bbox, so one
            # broken glyph — ⇌ has reported one five times a normal line's
            # height in both directions from where it actually sits, in both
            # syllabuses tried — drags the whole line's position toward it,
            # not just its height. Recomputed from whichever spans look like
            # ordinary text when at least one does not, rather than trusted.
            plausible = [
                s for s in line["spans"]
                if s["bbox"][3] - s["bbox"][1] < s["size"] * MAX_LINE_HEIGHT_RATIO
            ]
            if plausible and len(plausible) < len(line["spans"]):
                y0 = min(s["bbox"][1] for s in plausible)
                y1 = max(s["bbox"][3] for s in plausible)
            # Backstop for a line where every span is implausible, which
            # `plausible` cannot correct: still better than a fifth of a page.
            if y1 - y0 > span["size"] * MAX_LINE_HEIGHT_RATIO:
                y1 = y0 + span["size"] * 1.3
            lines.append(
                Line(
                    text=text,
                    x0=x0,
                    y0=y0,
                    y1=y1,
                    x1=x1,
                    size=span["size"],
                    bold="Bd" in span["font"] or "Bold" in span["font"],
                )
            )
    lines.sort(key=lambda line: (line.y0, line.x0))
    return lines


def fraction_bars(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """The horizontal rules that are fraction bars rather than table borders."""
    bars = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if (
            rect.height < BAR_MAX_HEIGHT
            and BAR_MIN_WIDTH < rect.width < BAR_MAX_WIDTH
            and rect.x0 > BODY_X - BAR_MIN_WIDTH
        ):
            bars.append(rect)
    return bars


def resolve_fractions(lines: list[Line], bars: list[pymupdf.Rect]) -> int:
    """Pair each fraction bar with the lines above and below it.

    Matched on the vertical centre of a line rather than its edges: these
    equations are set with tall boxes that overlap the bar from both sides, so
    "starts below the bar" is not true of the denominator and "ends above it" is
    not true of the numerator. The centre is unambiguous.

    Returns the number of bars nothing could be matched to. That is the number
    worth watching — an unresolved bar is an equation about to be flattened into
    prose that says something else.
    """
    unresolved = 0

    for bar in bars:
        middle = (bar.y0 + bar.y1) / 2
        over = [line for line in lines if _spans(line, bar) and line.middle < middle]
        under = [line for line in lines if _spans(line, bar) and line.middle > middle]
        if not over or not under:
            unresolved += 1
            continue

        numerator = min(over, key=lambda line: middle - line.middle)
        denominator = min(under, key=lambda line: line.middle - middle)
        # Two bars can share a numerator only if the template has changed; take
        # the first claim and report the second rather than overwriting it.
        if numerator.denominator is not None or denominator.is_denominator:
            unresolved += 1
            continue
        numerator.denominator = denominator
        denominator.is_denominator = True

    return unresolved


def _spans(line: Line, bar: pymupdf.Rect) -> bool:
    """Does this line sit over or under the bar, rather than beside it?"""
    return min(line.x1, bar.x1) - max(line.x0, bar.x0) > 0.5


def group_rows(lines: list[Line]) -> list[Row]:
    """Cluster vertically overlapping lines into rows, read left to right.

    A fraction is one token, positioned where its numerator starts, so it stays
    in one piece: numerator and denominator are centred on each other, and a
    denominator wider than its numerator starts further left. Ordering the parts
    by position alone would print the bottom of the fraction before the top.
    """
    clusters: list[list[Line]] = []
    for line in lines:
        current = clusters[-1] if clusters else None
        if current is not None and _overlaps_any(line, current):
            current.append(line)
        else:
            clusters.append([line])

    rows: list[Row] = []
    for cluster in clusters:
        tokens = [line for line in cluster if not line.is_denominator]
        # Rounded, so that two forms of the same equation set in the same column
        # — the worded one and the symbolic one under it — are ordered by which
        # is printed first rather than by a fraction of a point of kerning.
        tokens.sort(key=lambda line: (round(line.x0), line.y0))
        text = " ".join(
            line.text if line.denominator is None
            else f"{line.text} / {line.denominator.text}"
            for line in tokens
        )
        leftmost = min(cluster, key=lambda line: line.x0)
        rows.append(
            Row(
                text=clean(text),
                x0=leftmost.x0,
                y0=min(line.y0 for line in cluster),
                y1=max(line.y1 for line in cluster),
                size=leftmost.size,
                bold=leftmost.bold,
            )
        )
    return [row for row in rows if row.text]


def _overlaps_any(line: Line, cluster: list[Line]) -> bool:
    for member in cluster:
        overlap = min(line.y1, member.y1) - max(line.y0, member.y0)
        shorter = min(line.height, member.height)
        if shorter > 0 and overlap / shorter >= ROW_OVERLAP:
            return True
    return False


def page_rows(page: pymupdf.Page) -> tuple[list[Row], int, int]:
    """Body rows of one page, plus how many fraction bars were found and missed."""
    lines = page_lines(page)
    bars = fraction_bars(page)
    if len(bars) > MAX_BARS_PER_PAGE:
        bars = []
    unresolved = resolve_fractions(lines, bars)
    return group_rows(lines), len(bars), unresolved


def parse_syllabus(pdf_path: Path) -> Syllabus:
    """Read the subject-content chapter of a CAIE syllabus into a topic tree."""
    result = Syllabus()

    with pymupdf.open(pdf_path) as doc:
        result.subject_title, result.syllabus_code = _read_cover(doc)
        result.label, result.first_exam_year, result.last_exam_year = _read_years(doc)

        in_content = False
        section: Topic | None = None
        topic: Topic | None = None
        subtopic: Topic | None = None
        objectives: list[str] | None = None

        for page in doc:
            rows, bars, unresolved = page_rows(page)

            # Chapter headings are the only 18pt text in the document.
            for row in rows:
                if row.size >= CHAPTER_SIZE and row.bold:
                    heading = SECTION.match(row.text)
                    if heading:
                        in_content = heading.group(2).lower().startswith("subject content")
            if not in_content:
                continue

            result.fraction_bars += bars
            result.unresolved_bars += unresolved

            for row in rows:
                if row.size >= CHAPTER_SIZE:
                    continue

                if row.in_gutter and row.bold and row.size >= SECTION_SIZE:
                    match = SECTION.match(row.text)
                    if match:
                        code, title = match.group(1), CONTINUED.sub("", match.group(2))
                        section = _reuse_or_add(result.sections, code, title)
                        topic = subtopic = None
                        objectives = None
                        continue

                if row.in_gutter and row.bold:
                    match = TOPIC.match(row.text)
                    if match and section is not None:
                        code, title = match.group(1), CONTINUED.sub("", match.group(2))
                        topic = _reuse_or_add(section.children, code, title)
                        subtopic = None
                        objectives = topic.learning_objectives
                        continue

                if row.in_gutter and not row.bold:
                    match = SUBTOPIC.match(row.text)
                    if match and topic is not None:
                        code, title = match.group(1), CONTINUED.sub("", match.group(2))
                        subtopic = _reuse_or_add(topic.children, code, title)
                        objectives = subtopic.learning_objectives
                        continue

                    match = OBJECTIVE.match(row.text)
                    if match and objectives is not None:
                        objectives.append(match.group(2))
                        continue

                if objectives:
                    # The next line of a wrapped objective. Fractions were
                    # resolved into the row itself, so this is a plain join.
                    objectives[-1] = f"{objectives[-1]} {row.text}"

    result.problems = validate(result)
    return result


def _reuse_or_add(siblings: list[Topic], code: str, title: str) -> Topic:
    """Find the node for a code, or create it.

    Reuse is what makes 'continued' headings after a page break harmless: the
    same topic is met several times and has to accumulate objectives rather than
    replace itself.
    """
    for existing in siblings:
        if existing.code == code:
            return existing
    node = Topic(code=code, title=title, slug=slugify(title))
    siblings.append(node)
    return node


def _read_cover(doc: pymupdf.Document) -> tuple[str, str]:
    """Subject title and syllabus code, from the cover page."""
    text = doc[0].get_text("text")
    match = re.search(r"^(.*?)\s+(\d{4})\s*$", text, re.MULTILINE)
    return (match.group(1).strip(), match.group(2)) if match else ("", "")


def _read_years(doc: pymupdf.Document) -> tuple[str, int | None, int | None]:
    """The exam years this syllabus is for: 'Use this syllabus for exams in ...'.

    The label is what a student recognises ('2026-2028'), and the years are what
    decides which syllabus a paper is tagged against.
    """
    text = clean(doc[0].get_text("text"))
    match = re.search(r"for exams in ((?:\d{4}[,\s]*(?:and\s*)?)+)", text)
    if not match:
        return "", None, None
    years = [int(year) for year in re.findall(r"\d{4}", match.group(1))]
    if not years:
        return "", None, None
    first, last = min(years), max(years)
    label = str(first) if first == last else f"{first}-{last}"
    return label, first, last


def validate(syllabus: Syllabus) -> list[str]:
    """Everything that would make this tree wrong to load.

    A syllabus is a small, highly regular document, so anything unexpected here
    means the template has moved and the parse should be looked at rather than
    trusted. The cost of being wrong is not a bad page — it is every question in
    the subject tagged against a tree that does not match the syllabus.
    """
    problems: list[str] = []

    if not syllabus.sections:
        return ["no subject content found"]

    codes = [topic.code for topic in syllabus.walk()]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        problems.append(f"duplicate topic codes: {duplicates}")

    for topic in syllabus.walk():
        if topic.children and topic.learning_objectives:
            problems.append(
                f"{topic.code} has both sub-topics and its own learning objectives"
            )
        if not topic.children and not topic.learning_objectives:
            problems.append(f"{topic.code} {topic.title!r} has no learning objectives")

    numbers = [int(section.code) for section in syllabus.sections]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"sections are not 1..n: {numbers}")

    if syllabus.unresolved_bars:
        problems.append(
            f"{syllabus.unresolved_bars} of {syllabus.fraction_bars} fraction bars "
            "had no line beneath them — an equation would be flattened into prose "
            "that says something else"
        )

    return problems
