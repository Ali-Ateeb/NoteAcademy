"""Keep the public crop bucket equal to "the crops of approved questions".

Students load crops from a public bucket on Supabase's CDN instead of through
the gated `/api/asset` function (see web/src/lib/cropUrl.ts for why: 1-2.8 s
cold per crop through the function, against one CDN hop for a file). A public
bucket has no row level security, so what is in it *is* what the world can read.
That makes one rule matter: it holds crops of approved questions, and only those.

The source of truth stays the database and the private bucket. This module
makes the public bucket a projection of them, and is idempotent: run it again
and it finds nothing to do.

    copy    approved crops missing from the public bucket, or whose bytes
            differ from the private original (a re-rendered crop)
    remove  anything in the public bucket that is no longer an approved crop:
            a question un-approved or rejected, a crop deleted as spurious
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import psycopg

from .storage import StorageError, SupabaseStorage

log = logging.getLogger(__name__)

# Same window the gated route uses: a corrected re-render reaches everyone
# within the hour.
CACHE_CONTROL = "max-age=3600"


@dataclass
class SyncPlan:
    to_copy: list[str] = field(default_factory=list)
    to_remove: list[str] = field(default_factory=list)
    missing_source: list[str] = field(default_factory=list)
    unchanged: int = 0


@dataclass
class SyncReport:
    copied: int = 0
    removed: int = 0
    failed: list[str] = field(default_factory=list)


def plan_sync(
    approved: set[str],
    private: dict[str, str | None],
    public: dict[str, str | None],
) -> SyncPlan:
    """What to do so `public` holds exactly the approved crops.

    Each dict maps a storage key to that object's ETag (a content hash), or
    None when the ETag is unknown. An unknown ETag on either side counts as
    "changed": copying an unchanged crop again is harmless, leaving a stale one
    in place is not.
    """
    plan = SyncPlan()
    for key in sorted(approved):
        if key not in private:
            plan.missing_source.append(key)
            continue
        if key in public and public[key] is not None and public[key] == private[key]:
            plan.unchanged += 1
        else:
            plan.to_copy.append(key)
    plan.to_remove = sorted(set(public) - approved)
    return plan


def approved_crop_keys(conn: psycopg.Connection) -> set[str]:
    """Every crop key attached to an approved question."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct qa.storage_key
              from question_assets qa
              join questions q on q.id = qa.question_id
             where q.extraction_status = 'approved'
               and qa.kind = 'question_crop'
            """
        )
        return {row["storage_key"] for row in cur.fetchall()}


def bucket_crops(conn: psycopg.Connection, bucket: str) -> dict[str, str | None]:
    """{key: etag} for every crop object in a bucket, read from storage's own
    catalogue table in one query rather than listing folder by folder."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select name, metadata->>'eTag' as etag
              from storage.objects
             where bucket_id = %s and name like %s
            """,
            (bucket, "%/crops/%"),
        )
        return {row["name"]: row["etag"] for row in cur.fetchall()}


def make_plan(conn: psycopg.Connection, private_bucket: str, public_bucket: str) -> SyncPlan:
    return plan_sync(
        approved_crop_keys(conn),
        bucket_crops(conn, private_bucket),
        bucket_crops(conn, public_bucket),
    )


def _with_retries(action: Callable[[], None], attempts: int = 4) -> None:
    for attempt in range(attempts):
        try:
            return action()
        except StorageError:
            if attempt == attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def apply_plan(
    private: SupabaseStorage,
    public: SupabaseStorage,
    plan: SyncPlan,
    *,
    workers: int = 8,
    progress: Callable[[int, int], None] | None = None,
) -> SyncReport:
    """Carry out a plan. Failures are collected, not raised: one unreadable
    object must not stop the other thousand, and the next run picks up
    whatever this one could not."""
    report = SyncReport()

    def copy_one(key: str) -> str | None:
        try:
            data = private.download(key)
            _with_retries(lambda: public.put(key, data, "image/png", CACHE_CONTROL))
            return None
        except StorageError as error:
            log.warning("copying %s failed: %s", key, error)
            return key

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for failed in pool.map(copy_one, plan.to_copy):
            done += 1
            if failed:
                report.failed.append(failed)
            else:
                report.copied += 1
            if progress:
                progress(done, len(plan.to_copy))

    # Removals last, and in batches: a sweep should never leave the bucket
    # emptier than the plan says, even if a copy above failed.
    for start in range(0, len(plan.to_remove), 100):
        batch = plan.to_remove[start : start + 100]
        try:
            public.remove(batch)
            report.removed += len(batch)
        except StorageError as error:
            log.warning("removing %d crops failed: %s", len(batch), error)
            report.failed.extend(batch)
    return report
