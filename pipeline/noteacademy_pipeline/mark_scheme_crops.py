"""Attach a picture of each mark-scheme row to the question it marks.

Mathematics mark schemes are typeset maths in a table; extracting their text
scatters fractions and drops symbols (see `markscheme_maths.py`). The row itself,
rendered, is what a student can actually read, so it is stored as an asset of
kind `mark_scheme_crop` on the leaf question, the way a question's own crop is
stored, and served by the same route.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .load import attach_asset, clear_assets
from .markscheme_maths import mark_scheme_regions
from .naming import mark_scheme_crop_key
from .render import crop

log = logging.getLogger(__name__)

KIND = "mark_scheme_crop"


@dataclass
class MarkSchemeCropReport:
    attached: int = 0
    questions: int = 0
    unmatched_labels: list[str] = field(default_factory=list)
    # (storage key, local file) for the caller to upload.
    files: list[tuple[str, Path]] = field(default_factory=list)


def attach_mark_scheme_crops(
    conn: psycopg.Connection,
    paper_id: str,
    ms_pdf: Path,
    prefix: str,
    crops_dir: Path,
    *,
    dpi: int = 130,
) -> MarkSchemeCropReport:
    """Render every row of one paper's mark scheme and record it against the
    question it belongs to. Re-running replaces what was there. A row whose label
    the paper has no question for is reported, not attached to a guess."""
    report = MarkSchemeCropReport()
    regions = mark_scheme_regions(ms_pdf)

    with conn.cursor() as cur:
        cur.execute("select id, display_label from questions where paper_id = %s", (paper_id,))
        question_ids = {row["display_label"]: str(row["id"]) for row in cur.fetchall()}

    for label, boxes in regions.items():
        question_id = question_ids.get(label)
        if question_id is None:
            report.unmatched_labels.append(label)
            continue
        clear_assets(conn, question_id, KIND)
        for order, (page_number, bbox) in enumerate(boxes):
            key = mark_scheme_crop_key(prefix, label, order)
            path = crops_dir / Path(key).name
            # No padding: the box is already the row, and padding would pull in
            # the neighbouring row's border.
            crop(ms_pdf, page_number, bbox, path, dpi=dpi, padding_pt=0.0)
            attach_asset(
                conn, question_id, kind=KIND, storage_key=key, page_number=page_number,
                bbox=bbox, sort_order=order,
            )
            report.files.append((key, path))
            report.attached += 1
        report.questions += 1
    return report
