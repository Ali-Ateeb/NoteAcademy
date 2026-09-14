"""Crops that show the wrong question entirely, not just a missing file.

Discovered from a single student report of an "extra question" appearing
inside a structured question's crop. The actual cause: a structured
question's real content ends cleanly on one page — often visibly, with
"[Total: N]" printed with room to spare — but the segmenter still marks it
`continues_on_next_page`, then the stitching step latches onto that next
page's own header/barcode strip as if it were this question's continuation.
The bbox it grabs is always the same handful of points tall regardless of
subject, paper or year, which is why this is checkable at all: real content
is never that short, but a page header always is.

Unlike a missing crop (crops.py), there is no correct bbox to re-render here
— the content the crop shows belongs to a different question. The fix is
removing the row, not fixing the image.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

from .storage import SupabaseStorage

log = logging.getLogger(__name__)


@dataclass
class SpuriousCrop:
    asset_id: str
    question_id: str
    paper_slug: str
    display_label: str
    storage_key: str
    page_number: int
    height_pt: float


def find_spurious_continuation_crops(
    conn: psycopg.Connection, *, min_height_pt: float = 30.0
) -> list[SpuriousCrop]:
    """The last page-crop of a multi-page structured question, when it is too
    short to be real content.

    Only the *last* crop of a question with more than one: an early page
    being short is unremarkable (a figure-heavy page can end abruptly), but a
    short final page with nothing after it is the signature of this bug. 30pt
    is comfortably above the ~19pt a bare header strip measures and nowhere
    near the hundreds of points real trailing content runs — there is no
    observed case in between.
    """
    with conn.cursor() as cur:
        cur.execute("""
            with crops as (
              select qa.id as asset_id, qa.question_id, p.slug as paper_slug,
                     q.display_label, qa.storage_key, qa.page_number,
                     qa.bbox[4] - qa.bbox[2] as height_pt,
                     count(*) over (partition by qa.question_id) as crop_count,
                     row_number() over (
                       partition by qa.question_id order by qa.page_number desc
                     ) as rn
                from question_assets qa
                join questions q on q.id = qa.question_id
                join papers p on p.id = q.paper_id
               where q.question_type = 'structured'
                 and q.parent_question_id is null
                 and qa.kind = 'question_crop'
            )
            select asset_id, question_id, paper_slug, display_label,
                   storage_key, page_number, height_pt
              from crops
             where crop_count > 1 and rn = 1 and height_pt < %s
             order by paper_slug, display_label
        """, (min_height_pt,))
        return [
            SpuriousCrop(
                asset_id=str(row["asset_id"]),
                question_id=str(row["question_id"]),
                paper_slug=row["paper_slug"],
                display_label=row["display_label"],
                storage_key=row["storage_key"],
                page_number=row["page_number"],
                height_pt=row["height_pt"],
            )
            for row in cur.fetchall()
        ]


def remove_spurious_crops(
    conn: psycopg.Connection,
    storage: SupabaseStorage,
    crops: list[SpuriousCrop],
    *,
    dry_run: bool = False,
) -> int:
    """Delete the rows, then the now-unreferenced storage objects.

    Row first: a crash between the two steps leaves an orphaned file in the
    bucket, which costs nothing, rather than a database row pointing at a
    file that no longer exists, which is the exact bug this whole thing
    started from.
    """
    if not crops or dry_run:
        return len(crops)

    with conn.cursor() as cur:
        cur.execute(
            "delete from question_assets where id = any(%s)",
            ([c.asset_id for c in crops],),
        )

    try:
        storage.remove([c.storage_key for c in crops])
    except Exception:
        log.exception("removing storage objects failed; database rows are already gone")

    return len(crops)
