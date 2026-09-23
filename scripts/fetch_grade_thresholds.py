#!/usr/bin/env python3
"""Download grade-threshold tables from Cambridge's own official site.

Unlike past papers and mark schemes, Cambridge publishes grade thresholds
publicly and directly at cambridgeinternational.org -- no login, no embargo,
one small PDF (under 100KB) per subject per exam series. This is the first-
party source, not a redistributor, which is why this script fetches from
`cambridgeinternational.org` directly rather than through `fetch_papers.py`'s
third-party mirror.

The tradeoff that comes with a first-party source: Cambridge only keeps
about four years of series listed on the public page (June 2022 onward, as
of when this was written) rather than the full archive a mirror carries.
Sessions older than that are not available here at all.

How it works:

  1. The index page (`.../cambridge-o-level/grade-threshold-tables/`) links
     one page per exam series. Session slugs are read from that page rather
     than generated -- Cambridge's own slugs are not consistent enough to
     predict (`nov-2024` beside `november-2023`, `june2022` with no hyphen
     beside `june-2024`).
  2. Each series page lists one PDF per subject; the wanted ones are found
     by syllabus code appearing in the link, e.g. `-5054-`.
  3. Saved under this pipeline's own naming convention regardless of
     Cambridge's descriptive filenames, so `noteacademy load-grade-thresholds`
     can find them the same way it finds every other local paper file.

Also unhurried, for the same reason `fetch_papers.py` is: one request a
second, one at a time, identifying itself.

    python scripts/fetch_grade_thresholds.py 5054 5070 5090
    python scripts/fetch_grade_thresholds.py 5054 --dry-run

Copyright in these tables is Cambridge Assessment International Education's.
This downloads them; deciding what may be done with them afterwards is not
something a script settles.
"""

from __future__ import annotations

import argparse
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SITE = "https://www.cambridgeinternational.org"
INDEX = (
    f"{SITE}/programmes-and-qualifications/cambridge-upper-secondary/"
    "cambridge-o-level/grade-threshold-tables/"
)

USER_AGENT = (
    "NoteAcademy-grade-threshold-fetcher/1.0 "
    "(past-paper ingestion; contact the repository owner)"
)

SESSION_LINK = re.compile(
    r'href="(/programmes-and-qualifications/cambridge-upper-secondary/'
    r'cambridge-o-level/grade-threshold-tables/[a-z0-9-]+)"',
    re.IGNORECASE,
)
PDF_LINK = re.compile(r'href="(/Images/[^"]+?\.pdf)"', re.IGNORECASE)
SESSION_DATE = re.compile(r"(june|nov(?:ember)?)-?(\d{4})", re.IGNORECASE)


@dataclass(frozen=True)
class Session:
    year: int
    letter: str  # 's' (may/june) or 'w' (oct/nov), matching naming.py


def get(url: str, *, timeout: int = 60, retries: int = 3) -> bytes:
    """Fetch a URL, backing off on failure rather than hammering."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                raise
            last = error
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
        if attempt < retries - 1:
            time.sleep(2**attempt)

    raise RuntimeError(f"{url}: {last}")


def session_pages() -> dict[str, Session]:
    """Every exam series the index page currently links, keyed by its page path."""
    html = get(INDEX).decode("utf-8", errors="replace")
    pages: dict[str, Session] = {}
    for path in dict.fromkeys(SESSION_LINK.findall(html)):
        match = SESSION_DATE.search(path)
        if not match:
            continue
        letter = "s" if match.group(1).lower() == "june" else "w"
        pages[path] = Session(year=int(match.group(2)), letter=letter)
    return pages


def pdf_links_for(page_path: str, codes: list[str]) -> dict[str, str]:
    """Every wanted subject's PDF link on one series page, keyed by syllabus code."""
    html = get(f"{SITE}{page_path}").decode("utf-8", errors="replace")
    found: dict[str, str] = {}
    for href in PDF_LINK.findall(html):
        for code in codes:
            if f"-{code}-" in href:
                found[code] = f"{SITE}{href}"
    return found


def download(url: str, destination: Path) -> str:
    if destination.exists() and destination.stat().st_size > 0:
        return "have"

    body = get(url)
    if not body.startswith(b"%PDF"):
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
    parser.add_argument(
        "--out", type=Path, default=Path("papers"), help="Directory to download into."
    )
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pages = session_pages()
    print(f"{len(pages)} exam series currently listed")

    downloaded = skipped = failed = 0
    for path, session in sorted(pages.items(), key=lambda kv: kv[1].year, reverse=True):
        try:
            links = pdf_links_for(path, args.codes)
        except Exception as error:  # noqa: BLE001 - one series must not end the run
            print(f"  {path}: {error}")
            failed += 1
            continue
        time.sleep(args.delay)

        if not links:
            continue

        label = f"{session.letter}{session.year % 100:02d}"
        print(f"  {label}: {len(links)} of {len(args.codes)} subjects")
        for code, url in links.items():
            target = args.out / code / f"{code}_{label}_gt.pdf"
            if args.dry_run:
                print(f"    would fetch {target.name}")
                downloaded += 1
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = download(url, target)
            except Exception as error:  # noqa: BLE001
                print(f"    {target.name}: {error}")
                failed += 1
                continue

            if result == "have":
                skipped += 1
                continue
            print(f"    {target.name}  {result}")
            downloaded += 1
            time.sleep(args.delay)

    verb = "would download" if args.dry_run else "downloaded"
    print(f"\n{verb} {downloaded}, already had {skipped}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        raise SystemExit(130) from None
