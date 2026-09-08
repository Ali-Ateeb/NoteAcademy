"""CAIE's file naming, and ours.

Every past paper anyone will ever hand this pipeline is already named by
Cambridge: `5054_s19_qp_11.pdf` is syllabus 5054, the May/June 2019 session,
question paper, component 1, variant 1. Reading that is strictly better than
asking an operator to retype it as five flags — a backfill is thousands of
files, and a mistyped session silently files a paper under the wrong year,
where nobody will ever look for it.

The parse is deliberately strict. A filename that does not match is rejected
rather than guessed at, because the alternative is a paper loaded under a
plausible-looking wrong session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# CAIE's session letters. 'm' is the February/March series (South Asia only),
# 's' the May/June "summer" series, 'w' the October/November "winter" series.
SEASON_BY_LETTER = {"m": "feb_march", "s": "may_june", "w": "oct_nov"}

# The short forms used in storage keys.
SEASON_ABBREVIATION = {"feb_march": "fm", "may_june": "mj", "oct_nov": "on"}

FILENAME = re.compile(
    r"^(?P<syllabus_code>\d{4})"
    r"_(?P<season_letter>[msw])(?P<year>\d{2})"
    r"_(?P<doc_type>qp|ms|er|gt|in|ci)"
    r"(?:_(?P<paper>\d{1,2}))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PaperFile:
    """What a CAIE filename says about the document inside it."""

    syllabus_code: str
    year: int
    season: str
    doc_type: str
    component: int | None
    variant: int | None

    @property
    def session_slug(self) -> str:
        return f"{self.year}-{self.season.replace('_', '-')}"


def expand_year(two_digit: int) -> int:
    """CAIE writes the year with two digits and has done since the 1990s.

    Anything from 90 up is last century: `5054_w98_qp_1` is 1998, not 2098. The
    boundary has to sit above the current year plus the couple of years of
    future sessions that are published in advance — 89 leaves that room and
    stops being right in 2090, by which time this is not the problem.
    """
    return 1900 + two_digit if two_digit >= 90 else 2000 + two_digit


def parse_paper_filename(stem: str) -> PaperFile:
    """Read a CAIE document filename. Raises ValueError if it is not one."""
    match = FILENAME.match(stem.strip())
    if match is None:
        raise ValueError(
            f"{stem!r} is not a CAIE document name "
            "(expected e.g. '5054_s19_qp_11', '5054_w15_ms_12', '5054_s19_gt')"
        )

    paper = match.group("paper")
    component: int | None = None
    variant: int | None = None
    if paper:
        # '11' is component 1 variant 1; '1' is a component with no variants,
        # which is how single-variant and older sittings are published.
        component = int(paper[0])
        variant = int(paper[1]) if len(paper) == 2 else None

    return PaperFile(
        syllabus_code=match.group("syllabus_code"),
        year=expand_year(int(match.group("year"))),
        season=SEASON_BY_LETTER[match.group("season_letter").lower()],
        doc_type=match.group("doc_type").lower(),
        component=component,
        variant=variant,
    )


SEASON_LETTERS = {season: letter for letter, season in SEASON_BY_LETTER.items()}


def caie_filename(
    syllabus_code: str,
    year: int,
    season: str,
    doc_type: str,
    component: int | None = None,
    variant: int | None = None,
) -> str:
    """Rebuild the name Cambridge gave a document.

    The inverse of `parse_paper_filename`, for going the other way: from a paper
    row in the database back to the file it came from, without recording a path
    that stops being true the moment the corpus moves.
    """
    paper = ""
    if component is not None:
        paper = f"_{component}{'' if variant is None else variant}"
    return (
        f"{syllabus_code}_{SEASON_LETTERS[season]}{year % 100:02d}"
        f"_{doc_type}{paper}.pdf"
    )


def storage_prefix(
    syllabus_code: str, year: int, season: str, component: int, variant: int | None
) -> str:
    """Where a paper's files live in object storage.

    A stable, readable key rather than a UUID: when something looks wrong in the
    viewer, the person debugging it should be able to tell which paper a key
    refers to without a database round trip.
    """
    variant_part = "" if variant is None else str(variant)
    return (
        f"papers/{syllabus_code}/{year}-{SEASON_ABBREVIATION[season]}"
        f"/p{component}{variant_part}"
    )


def document_key(prefix: str, doc_type: str) -> str:
    return f"{prefix}/{doc_type}.pdf"


def crop_key(prefix: str, question_number: int) -> str:
    return f"{prefix}/crops/{question_number}.png"
