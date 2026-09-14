"""Recover crops that a database row points at but the bucket does not have.

`question_assets.bbox` is kept in PDF points precisely so a crop can be
re-rendered without re-running layout analysis (see 0004_questions.sql) — this
is that recovery path. It is pure and cheap: no model call, no ambiguity about
correctness, because the bbox and page number are already right. The only
thing missing is the image file itself, so this re-crops the source PDF and
re-uploads it to the exact key the database already names.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .config import settings
from .naming import caie_filename
from .render import crop
from .storage import SupabaseStorage

log = logging.getLogger(__name__)


def _list_folder(storage: SupabaseStorage, prefix: str, attempts: int = 5) -> set[str]:
    """Names present under one storage prefix. One call per paper's crop
    folder rather than one per file — a paper of even 79 assets is one round
    trip, not 79."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            body = json.dumps({"prefix": prefix, "limit": 1000}).encode()
            req = urllib.request.Request(
                f"{storage.url}/storage/v1/object/list/{storage.bucket}",
                method="POST",
                data=body,
                headers={
                    "apikey": storage.service_role_key,
                    "Authorization": f"Bearer {storage.service_role_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return {item["name"] for item in json.loads(resp.read())}
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"listing {prefix} failed after {attempts} attempts") from last_exc


@dataclass
class MissingCrop:
    question_id: str
    storage_key: str
    page_number: int
    bbox: tuple[float, float, float, float]
    subject_slug: str
    syllabus_code: str
    year: int
    season: str
    component: int
    variant: int | None


@dataclass
class FixReport:
    checked_folders: int = 0
    found_missing: int = 0
    fixed: int = 0
    no_source_pdf: list[str] = field(default_factory=list)
    crop_failed: list[str] = field(default_factory=list)
    upload_failed: list[str] = field(default_factory=list)


def find_missing_crops(conn: psycopg.Connection, storage: SupabaseStorage) -> tuple[list[MissingCrop], int]:
    """Every approved question's crop the database names, cross-checked
    against what the bucket actually holds. Grouped by folder so a paper's
    worth of crops costs one list call, not one per crop."""
    with conn.cursor() as cur:
        cur.execute("""
            select qa.question_id, qa.storage_key, qa.page_number, qa.bbox,
                   s.slug as subject_slug, s.syllabus_code,
                   es.year, es.season, p.component, p.variant
              from question_assets qa
              join questions q on q.id = qa.question_id
              join papers p on p.id = q.paper_id
              join subjects s on s.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
             -- Not 'rejected': a dead question is never shown to anyone again,
             -- so a missing crop there costs nothing to leave alone. Everything
             -- else is either live to students or sitting in front of a
             -- reviewer right now, and a reviewer cannot approve what they
             -- cannot see.
             where q.extraction_status in ('approved', 'needs_review', 'extracted')
        """)
        rows = cur.fetchall()

    by_folder: dict[str, list[dict]] = {}
    for row in rows:
        folder = row["storage_key"].rsplit("/", 1)[0]
        by_folder.setdefault(folder, []).append(row)

    missing: list[MissingCrop] = []
    checked = 0
    for folder, items in by_folder.items():
        present = _list_folder(storage, folder + "/")
        checked += 1
        for row in items:
            filename = row["storage_key"].rsplit("/", 1)[1]
            if filename in present:
                continue
            missing.append(
                MissingCrop(
                    question_id=str(row["question_id"]),
                    storage_key=row["storage_key"],
                    page_number=row["page_number"],
                    bbox=tuple(row["bbox"]),
                    subject_slug=row["subject_slug"],
                    syllabus_code=row["syllabus_code"],
                    year=row["year"],
                    season=row["season"],
                    component=row["component"],
                    variant=row["variant"],
                )
            )

    return missing, checked


def fix_missing_crops(
    missing: list[MissingCrop],
    *,
    papers_dir: Path,
    storage: SupabaseStorage,
    work_dir: Path,
    dpi: int = 150,
    dry_run: bool = False,
) -> FixReport:
    """Re-render each missing crop from its source PDF and upload it.

    Grouped by source PDF so a paper of many missing crops opens that PDF
    once, not once per crop.
    """
    report = FixReport(found_missing=len(missing))

    by_paper: dict[tuple, list[MissingCrop]] = {}
    for item in missing:
        key = (item.syllabus_code, item.year, item.season, item.component, item.variant)
        by_paper.setdefault(key, []).append(item)

    for (code, year, season, component, variant), items in by_paper.items():
        qp_name = caie_filename(code, year, season, "qp", component, variant)
        qp_path = papers_dir / code / qp_name
        if not qp_path.is_file():
            report.no_source_pdf.extend(i.storage_key for i in items)
            log.warning("no source PDF for %s (expected %s)", qp_name, qp_path)
            continue

        for item in items:
            out_path = work_dir / "crop-recovery" / item.storage_key
            try:
                crop(qp_path, item.page_number, item.bbox, out_path, dpi=dpi)
            except Exception:
                log.exception("crop failed for %s", item.storage_key)
                report.crop_failed.append(item.storage_key)
                continue

            if dry_run:
                report.fixed += 1
                continue

            try:
                storage.upload(item.storage_key, out_path)
                report.fixed += 1
            except Exception:
                log.exception("upload failed for %s", item.storage_key)
                report.upload_failed.append(item.storage_key)

    return report
