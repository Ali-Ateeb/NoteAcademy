"""The write half of the automated multiple-choice verifier, made survivable.

`verify_mcqs_with_model` does two very different jobs: hundreds of vision
calls (expensive, slow, and safe to lose nothing from) and then a burst of
database writes (cheap, fast, and over a link that does drop). It used to do
the second as a single transaction on a single connection, with the model's
answers held only in memory -- so one `server closed the connection
unexpectedly` (which is what ended the chemistry run, 1,474 questions in)
rolled back every write and threw the answers away with it.

Now the answers are saved to disk before the first write, each canonical
question (with its duplicates) commits on its own, a lost connection is
re-opened and the same group retried, and `resume_mcqs_from_results` replays a
saved file without calling the model again. The structured verifier
(`verify_structured.py`) has had the save-and-replay half of this since an
earlier run failed the same way; this adds the part it lacks -- reconnecting
mid-replay instead of failing every remaining row against a dead connection.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from .config import settings
from .verify import VerifyReport, _apply_one

log = logging.getLogger(__name__)

_COUNTERS = (
    "agreed", "disagreed", "skipped_no_decision", "skipped_approved", "disagreed_approved",
)


def save_mcq_results(subject_slug: str, answers: list[dict], path: Path | None = None) -> Path:
    """Persist the model's answers, one JSON object per canonical question.

    Each carries the ids of the duplicates its answer applies to, as they stand
    at the time, so a replay needs neither the source PDFs nor the grouping.
    """
    path = path or (settings.work_dir / f"verify-mcq-{subject_slug}-results.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for answer in answers:
            handle.write(json.dumps(answer, ensure_ascii=False) + "\n")
    return path


def _merge_counts(into: VerifyReport, part: VerifyReport) -> None:
    for name in _COUNTERS:
        setattr(into, name, getattr(into, name) + getattr(part, name))
    into.approved_disagreements.extend(part.approved_disagreements)


# SQLSTATEs where the server is ending the session rather than refusing a
# statement: administrator shutdown, crash shutdown, "cannot connect now".
_SESSION_ENDING = {"57P01", "57P02", "57P03"}


def _link_lost(error: psycopg.OperationalError, conn: psycopg.Connection) -> bool:
    """Is the connection itself gone, as opposed to one statement refused?

    Decided by the error, not by the connection's flags -- found the hard way:
    with a socket severed under a live connection psycopg raises an
    OperationalError but leaves `conn.closed` and `conn.broken` both False, so
    a check on those treated a dead link as a healthy one, rolled back on it,
    and then failed every remaining group ("another command is already in
    progress"). A failure of the link has no SQLSTATE (there was no server
    reply to carry one); a refusal by the server always does (a statement
    timeout is 57014, a lock 55P03). The flags stay as a backstop.
    """
    state = error.sqlstate
    return (
        state is None
        or state.startswith("08")
        or state in _SESSION_ENDING
        or conn.closed
        or conn.broken
    )


def _rollback_quietly(conn: psycopg.Connection) -> None:
    try:
        conn.rollback()
    except psycopg.Error:
        pass


def _close_quietly(conn: psycopg.Connection) -> None:
    try:
        conn.close()
    except psycopg.Error:
        pass


def commit_each(
    conn: psycopg.Connection,
    units: list,
    *,
    apply_unit: Callable[[psycopg.Connection, Any, Any], None],
    new_part: Callable[[], Any],
    merge: Callable[[Any, Any], None],
    label: Callable[[Any], str],
    report: Any,
    dry_run: bool = False,
    lock_timeout_ms: int = 4000,
    max_reconnects: int = 5,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Apply each unit of work in its own transaction, surviving a dropped link.

    Shared by both automated verifiers' write passes. Takes ownership of
    `conn` and closes it -- or whichever connection replaced it -- when done.

      * each unit commits on its own, so a failure costs that unit, not the
        run -- and the appliers re-read current state, so replaying a unit
        that already landed just reads as agreement with itself;
      * a lost connection is re-opened and *the same unit retried*, up to
        `max_reconnects` times in all, with a backoff between;
      * a short `lock_timeout` skips a row another session is holding instead
        of stalling the pass until the statement timeout kills it;
      * an applier writes into a fresh scratch report, merged into `report`
        only once the unit has committed, so a retried unit is never counted
        twice.

    If the database stays unreachable past `max_reconnects`, the remaining
    units are listed in `report.failed_writes` and the pass ends; the saved
    results file is what replays them. `report` needs `skipped_locked`,
    `failed_writes` and `reconnects`.
    """
    from .load import connect

    # Looked up now, not as a default argument, so it can be substituted.
    sleep = sleep or time.sleep

    try:
        for index, unit in enumerate(units):
            while True:
                part = new_part()
                try:
                    with conn.cursor() as cur:
                        # SET takes no bind parameters; the value is an int.
                        cur.execute(f"set local lock_timeout = {int(lock_timeout_ms)}")
                    apply_unit(conn, unit, part)
                    if dry_run:
                        conn.rollback()
                    else:
                        conn.commit()
                    merge(report, part)
                    break
                except psycopg.errors.LockNotAvailable:
                    _rollback_quietly(conn)
                    report.skipped_locked += 1
                    log.warning("%s is locked by another session; skipped", label(unit))
                    break
                except psycopg.OperationalError as error:
                    if not _link_lost(error, conn):
                        # The server refused (a statement timeout, say) but the
                        # link is fine: this unit's failure, nobody else's.
                        _rollback_quietly(conn)
                        report.failed_writes.append(label(unit))
                        log.warning("%s failed to write: %s", label(unit), error)
                        break

                    # The link itself is gone. Re-open it and retry this unit.
                    if report.reconnects >= max_reconnects:
                        report.failed_writes.extend(label(u) for u in units[index:])
                        log.error(
                            "database unreachable after %d reconnects; %d unit(s) not "
                            "written. Replay them from the saved results file.",
                            max_reconnects, len(units) - index,
                        )
                        return
                    report.reconnects += 1
                    log.warning(
                        "connection lost (%s); reconnecting (%d/%d)",
                        error, report.reconnects, max_reconnects,
                    )
                    _close_quietly(conn)
                    sleep(min(2**report.reconnects, 15))
                    try:
                        conn = connect(settings.database_url)
                    except psycopg.OperationalError as reconnect_error:
                        # `conn` stays the closed one, so the next pass round
                        # this loop lands here again and counts another try.
                        log.warning("reconnect failed: %s", reconnect_error)
                except psycopg.Error as error:
                    _rollback_quietly(conn)
                    report.failed_writes.append(label(unit))
                    log.warning("%s failed to write: %s", label(unit), error)
                    break
    finally:
        _close_quietly(conn)


def write_mcq_decisions(
    subject_slug: str,
    answers: list[dict],
    confidence_floor: float,
    report: VerifyReport,
    *,
    dry_run: bool = False,
    lock_timeout_ms: int = 4000,
    max_reconnects: int = 5,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Apply saved multiple-choice answers, one canonical question (and its
    duplicates) per transaction. See `commit_each`."""
    from .load import connect

    conn = connect(settings.database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.code, t.id
                  from topics t
                  join syllabus_versions sv on sv.id = t.syllabus_version_id
                  join subjects s on s.id = sv.subject_id
                 where s.slug = %s and sv.is_current
                """,
                (subject_slug,),
            )
            topic_ids = {row["code"]: row["id"] for row in cur.fetchall()}
    except BaseException:
        _close_quietly(conn)
        raise

    units = []
    for answer in answers:
        if answer["topic_code"] not in topic_ids:
            report.unknown_codes.append(answer["topic_code"])
        else:
            units.append(answer)

    def apply_group(conn: psycopg.Connection, answer: dict, part: VerifyReport) -> None:
        for question_id in answer["group"]:
            _apply_one(
                conn, question_id, answer["topic_code"], topic_ids,
                confidence_floor, part, leave_approved=True,
            )

    commit_each(
        conn, units,
        apply_unit=apply_group, new_part=VerifyReport, merge=_merge_counts,
        label=lambda a: f"{a['paper_slug']} {a['display_label']}", report=report,
        dry_run=dry_run, lock_timeout_ms=lock_timeout_ms,
        max_reconnects=max_reconnects, sleep=sleep,
    )


def resume_mcqs_from_results(
    subject_slug: str,
    results_path: Path,
    *,
    confidence_floor: float,
    dry_run: bool = False,
    lock_timeout_ms: int = 4000,
) -> VerifyReport:
    """Replay answers saved by an earlier run through the ordinary write pass,
    without calling the model again.

    Safe to run repeatedly: every question is compared against its *current*
    tag, so one that already landed reads as agreeing with itself.
    """
    answers = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = VerifyReport(results_path=results_path)
    write_mcq_decisions(
        subject_slug, answers, confidence_floor, report,
        dry_run=dry_run, lock_timeout_ms=lock_timeout_ms,
    )
    return report


def write_approved_disagreements_csv(report: VerifyReport, path: Path) -> Path | None:
    """Which approved questions the model disagreed with, by paper and label.

    The report used to carry only a count of them, and the count vanished when
    the process exited -- leaving roughly a hundred questions flagged and no way
    to find out which. Returns None when there were none.
    """
    from .load import connect

    if not report.approved_disagreements:
        return None

    ids = [question_id for question_id, _, _ in report.approved_disagreements]
    with connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select q.id, p.slug as paper_slug, q.display_label
              from questions q join papers p on p.id = q.paper_id
             where q.id = any(%s::uuid[])
            """,
            (ids,),
        )
        where = {
            str(row["id"]): (row["paper_slug"], row["display_label"]) for row in cur.fetchall()
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["paper_slug", "display_label", "tag_on_file", "model_tag", "question_id"])
        for question_id, on_file, model in sorted(
            report.approved_disagreements, key=lambda r: where.get(str(r[0]), ("", ""))
        ):
            paper_slug, display_label = where.get(str(question_id), ("?", "?"))
            writer.writerow([paper_slug, display_label, on_file, model, question_id])
    return path
