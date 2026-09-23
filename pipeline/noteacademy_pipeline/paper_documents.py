"""Upload a paper's own PDFs (question paper, mark scheme, ...) to the bucket.

`paper_documents.storage_key` has been recorded at ingest time for every
document since `record_document` started being called — the row exists, the
checksum and page count are already right, only the file itself was never
sent to the bucket. This is that upload path, structured exactly like
`crops.py`'s crop recovery: audit what the bucket actually has under each
paper's folder, then upload only what is missing, straight from the local
source PDF the row was ingested from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .naming import caie_filename
from .storage import SupabaseStorage

log = logging.getLogger(__name__)


@dataclass
class MissingDocument:
    storage_key: str
    doc_type: str
    subject_slug: str
    syllabus_code: str
    year: int
    season: str
    component: int
    variant: int | None


@dataclass
class UploadReport:
    checked_folders: int = 0
    found_missing: int = 0
    uploaded: int = 0
    no_source_pdf: list[str] = field(default_factory=list)
    upload_failed: list[str] = field(default_factory=list)


def find_missing_documents(
    conn: psycopg.Connection, storage: SupabaseStorage
) -> tuple[list[MissingDocument], int]:
    """Every recorded document, cross-checked against what the bucket holds.

    Grouped by folder so a paper's two or three documents cost one list
    call, not one per document.
    """
    with conn.cursor() as cur:
        cur.execute("""
            select pd.storage_key, pd.doc_type::text doc_type,
                   s.slug as subject_slug, s.syllabus_code,
                   es.year, es.season, p.component, p.variant
              from paper_documents pd
              join papers p on p.id = pd.paper_id
              join subjects s on s.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
        """)
        rows = cur.fetchall()

    by_folder: dict[str, list[dict]] = {}
    for row in rows:
        folder = row["storage_key"].rsplit("/", 1)[0]
        by_folder.setdefault(folder, []).append(row)

    missing: list[MissingDocument] = []
    checked = 0
    for folder, items in by_folder.items():
        present = storage.list_folder(folder + "/")
        checked += 1
        for row in items:
            filename = row["storage_key"].rsplit("/", 1)[1]
            if filename in present:
                continue
            missing.append(
                MissingDocument(
                    storage_key=row["storage_key"],
                    doc_type=row["doc_type"],
                    subject_slug=row["subject_slug"],
                    syllabus_code=row["syllabus_code"],
                    year=row["year"],
                    season=row["season"],
                    component=row["component"],
                    variant=row["variant"],
                )
            )

    return missing, checked


def upload_missing_documents(
    missing: list[MissingDocument],
    *,
    papers_dir: Path,
    storage: SupabaseStorage,
    dry_run: bool = False,
) -> UploadReport:
    """Upload each missing document straight from its local source PDF."""
    report = UploadReport(found_missing=len(missing))

    for item in missing:
        # A grade-threshold PDF covers a whole session, not one component --
        # every paper in the session gets its own `paper_documents` row (and
        # its own copy in the bucket, at that paper's own prefix), but there
        # is only one local source file, named with no component at all.
        component = None if item.doc_type == "gt" else item.component
        variant = None if item.doc_type == "gt" else item.variant
        name = caie_filename(
            item.syllabus_code, item.year, item.season, item.doc_type, component, variant,
        )
        path = papers_dir / item.syllabus_code / name
        if not path.is_file():
            report.no_source_pdf.append(item.storage_key)
            log.warning("no source PDF for %s (expected %s)", item.storage_key, path)
            continue

        if dry_run:
            report.uploaded += 1
            continue

        try:
            storage.upload(item.storage_key, path)
            report.uploaded += 1
        except Exception:
            log.exception("upload failed for %s", item.storage_key)
            report.upload_failed.append(item.storage_key)

    return report
