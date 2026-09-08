#!/usr/bin/env python3
"""Download CAIE past papers from ivyonline.co, ready for `noteacademy load-mcq`.

The ingestion pipeline reads Cambridge's own filenames — `5054_s19_qp_11.pdf` —
to work out the session, component and variant, and this site serves files under
exactly those names, so what lands on disk is what the pipeline expects with no
renaming step in between.

How it works, and why this way:

  1. A session page (`/past-papers/o-level/5054/may-jun-19`) is server-rendered
     and already contains direct links to `files.ivyonline.co`. So the papers
     are read from the pages a browser would load, not from the site's internal
     JSON API — which its robots.txt disallows (`Disallow: /api/`) and which
     this script therefore never touches.
  2. Session slugs are generated rather than discovered: `s`->may-jun,
     `w`->oct-nov, `m`->feb-mar. A session that does not exist returns a page
     with no links and contributes nothing, which is cheaper than a discovery
     request and cannot silently miss a session that exists.

It is deliberately unhurried: one request a second by default, one at a time.
This is someone else's bandwidth, the whole corpus is a few gigabytes, and there
is no deadline. Re-running skips what is already downloaded, so an interrupted
run costs nothing.

    python scripts/fetch_papers.py 5054                       # everything
    python scripts/fetch_papers.py 5054 --components 1        # Paper 1 only
    python scripts/fetch_papers.py 5054 --components 1 --from-year 2008 --to-year 2012
    python scripts/fetch_papers.py 5070 5090 --components 1   # chemistry, biology
    python scripts/fetch_papers.py 5054 --dry-run             # list, download nothing

The multiple-choice ingestion needs a question paper and its mark scheme, which
is the default `--doc-types qp ms`. Add `er` for examiner reports once questions
carry them, or `gt` for grade thresholds.

Copyright in these papers is Cambridge Assessment International Education's.
This downloads them; deciding what may be done with them afterwards is not
something a script settles.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SITE = "https://ivyonline.co"
FILES_HOST = "files.ivyonline.co"

# CAIE's session letters, and this site's slug for each.
SEASON_SLUGS = {"m": "feb-mar", "s": "may-jun", "w": "oct-nov"}

# Identifies the script rather than impersonating a browser. If this ever needs
# to be blocked, whoever blocks it should be able to see what it is.
USER_AGENT = (
    "NoteAcademy-paper-fetcher/1.0 "
    "(past-paper ingestion; contact the repository owner)"
)

PDF_LINK = re.compile(
    rf"https://{re.escape(FILES_HOST)}/[A-Za-z0-9/_.-]+?\.pdf", re.IGNORECASE
)

# 5054_s19_qp_11.pdf -> syllabus, season, year, doc type, paper number.
FILENAME = re.compile(
    r"^(?P<code>\d{4})_(?P<season>[msw])(?P<year>\d{2})"
    r"_(?P<doc>[a-z]{2})(?:_(?P<paper>\d{1,2}))?\.pdf$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Paper:
    url: str
    filename: str
    year: int
    doc_type: str
    component: int | None

    @classmethod
    def parse(cls, url: str) -> Paper | None:
        """Read what a URL says about the document, or None if it is not one."""
        name = url.rsplit("/", 1)[-1]
        match = FILENAME.match(name)
        if not match:
            return None

        two_digit = int(match.group("year"))
        paper = match.group("paper")
        return cls(
            url=url,
            filename=name.lower(),
            # Same rule as the pipeline's own parser: CAIE has used two-digit
            # years since the 1990s, so anything from 90 up is last century.
            year=1900 + two_digit if two_digit >= 90 else 2000 + two_digit,
            doc_type=match.group("doc").lower(),
            component=int(paper[0]) if paper else None,
        )


def get(url: str, *, timeout: int = 60, retries: int = 3) -> bytes:
    """Fetch a URL, backing off on failure rather than hammering."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            # 404 means the paper is not there. Retrying will not change that.
            if error.code in (404, 410):
                raise
            last = error
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    raise RuntimeError(f"{url}: {last}")


def session_slugs(first_year: int, last_year: int) -> list[str]:
    """Every session slug in a year range, newest first.

    Generated rather than discovered: a session that does not exist serves a
    page with no links, which costs one request and cannot miss one that does.
    """
    slugs = []
    for year in range(last_year, first_year - 1, -1):
        for season in ("w", "s", "m"):
            slugs.append(f"{SEASON_SLUGS[season]}-{year % 100:02d}")
    return slugs


def papers_in_session(level: str, code: str, slug: str) -> list[Paper]:
    """Every paper linked from one session page."""
    try:
        html = get(f"{SITE}/past-papers/{level}/{code}/{slug}").decode(
            "utf-8", errors="replace"
        )
    except urllib.error.HTTPError:
        return []

    found: dict[str, Paper] = {}
    for url in PDF_LINK.findall(html):
        paper = Paper.parse(url)
        # Keyed by filename: a page lists the same document more than once.
        if paper and paper.filename not in found:
            found[paper.filename] = paper
    return list(found.values())


def wanted(paper: Paper, args: argparse.Namespace) -> bool:
    if paper.doc_type not in args.doc_types:
        return False
    if not (args.from_year <= paper.year <= args.to_year):
        return False
    if args.components:
        # Grade thresholds and examiner reports cover a whole session and name
        # no component; a component filter should not throw them away.
        return paper.component is None or paper.component in args.components
    return True


def download(paper: Paper, destination: Path) -> str:
    """Fetch one paper. Returns what happened, for the log.

    Written to a temporary name and moved into place, so an interrupted run
    never leaves a half-file that the next run would skip as already done.
    """
    if destination.exists() and destination.stat().st_size > 0:
        return "have"

    body = get(paper.url)
    if not body.startswith(b"%PDF"):
        # An error page served with a 200, which is common enough to check for:
        # saved silently it would fail much later, inside the pipeline.
        return f"not a PDF ({len(body)} bytes)"

    partial = destination.with_suffix(".partial")
    partial.write_bytes(body)
    partial.replace(destination)
    return f"{len(body) / 1024:.0f} KB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("codes", nargs="+", help="Syllabus codes, e.g. 5054 5070.")
    parser.add_argument("--level", default="o-level",
                        help="Qualification path segment (default: o-level).")
    parser.add_argument("--out", type=Path, default=Path("papers"),
                        help="Directory to download into (default: papers/).")
    parser.add_argument("--doc-types", nargs="+", default=["qp", "ms"],
                        help="qp ms er gt ci in (default: qp ms).")
    parser.add_argument("--components", nargs="*", type=int, default=[],
                        help="Component numbers, e.g. 1. Default: all.")
    parser.add_argument("--from-year", type=int, default=2008)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between requests (default: 1).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be downloaded, fetch nothing.")
    args = parser.parse_args()
    args.doc_types = [doc.lower() for doc in args.doc_types]

    slugs = session_slugs(args.from_year, args.to_year)
    downloaded = skipped = failed = 0

    for code in args.codes:
        target = args.out / code
        if not args.dry_run:
            target.mkdir(parents=True, exist_ok=True)

        print(f"\n{code}: {len(slugs)} sessions to check -> {target}")
        for slug in slugs:
            try:
                papers = papers_in_session(args.level, code, slug)
            except Exception as error:  # noqa: BLE001 - one session must not end the run
                print(f"  {slug}: {error}")
                failed += 1
                continue
            time.sleep(args.delay)

            keep = sorted(
                (p for p in papers if wanted(p, args)), key=lambda p: p.filename
            )
            if not keep:
                continue

            print(f"  {slug}: {len(keep)} of {len(papers)}")
            for paper in keep:
                if args.dry_run:
                    print(f"    would fetch {paper.filename}")
                    downloaded += 1
                    continue
                try:
                    result = download(paper, target / paper.filename)
                except Exception as error:  # noqa: BLE001
                    print(f"    {paper.filename}: {error}")
                    failed += 1
                    continue

                if result == "have":
                    skipped += 1
                    continue
                print(f"    {paper.filename}  {result}")
                downloaded += 1
                time.sleep(args.delay)

    verb = "would download" if args.dry_run else "downloaded"
    print(f"\n{verb} {downloaded}, already had {skipped}, failed {failed}")
    if downloaded and not args.dry_run:
        print(
            "\nNext:  noteacademy load-mcq "
            f"{args.out}/{args.codes[0]}/<code>_<session>_qp_<paper>.pdf "
            f"{args.out}/{args.codes[0]}/<code>_<session>_ms_<paper>.pdf"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        raise SystemExit(130) from None
