"""Recover a structured leaf's max_marks by reading the crop, not the API.

max_marks and mark_scheme_text come from two different extraction passes —
the mark bracket "[N]" printed on the question paper, and the marking points
matched from the mark scheme document — and for 945 leaves (920 already
approved) the first one never landed while the second did. Neither
arithmetic (the parent's own total is only ever set when it has zero parts —
see 0026) nor a text pattern on the mark scheme (only ~32% end in a clean
trailing number) recovers more than a fraction reliably.

The number is genuinely printed on the page. This renders the parent
question's own crop locally from the already-correct bbox (no vision call,
no cost) so a person — or Claude, reading the image directly — can read the
bracket for each affected leaf and answer once per parent rather than once
per leaf.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psycopg

from .naming import caie_filename
from .render import crop

log = logging.getLogger(__name__)


@dataclass
class LeafToRead:
    leaf_id: str
    display_label: str
    mark_scheme_text: str


@dataclass
class ParentToRead:
    parent_id: str
    paper_slug: str
    display_label: str
    crop_paths: list[str]
    leaves: list[LeafToRead]


@dataclass
class Decision:
    leaf_id: str
    max_marks: int


def _fetch_affected(conn: psycopg.Connection, limit: int | None) -> list[dict]:
    with conn.cursor() as cur:
        # A leaf's immediate parent_question_id is not necessarily the row
        # the crop is attached to: nesting can run leaf -> "9(c)" -> "9", and
        # question_assets only ever lives on the true top-level row (0022's
        # reviewable unit). The top-level's own display_label is always the
        # leaf's label up to its first "(", so that — not parent_question_id
        # — is what finds the row with crops to render.
        cur.execute(
            """
            select q.id as leaf_id, q.display_label, q.mark_scheme_text,
                   top.id as top_id, top.display_label as top_display_label,
                   p.slug as paper_slug,
                   s.syllabus_code, es.year, es.season, p.component, p.variant
              from questions q
              join papers p on p.id = q.paper_id
              join subjects s on s.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
              join questions top
                on top.paper_id = q.paper_id
               and top.parent_question_id is null
               and top.display_label = split_part(q.display_label, '(', 1)
             where q.question_type = 'structured'
               and q.mark_scheme_text is not null and q.mark_scheme_text <> ''
               and q.max_marks is null
               and not exists (select 1 from questions c where c.parent_question_id = q.id)
             order by top.id, q.ordinal
            """
        )
        rows = cur.fetchall()
    return rows[:limit] if limit else rows


def build_marks_worksheet(
    conn: psycopg.Connection,
    *,
    papers_dir: Path,
    work_dir: Path,
    limit: int | None = None,
) -> list[ParentToRead]:
    """Group affected leaves by parent question and render each parent's
    crops locally, ready to be read.

    Rendered fresh each time rather than reused from the bucket: the parent's
    bbox is already known-correct (these are not the spurious-crop rows
    removed earlier), so there is nothing to gain from a network round trip
    that a local re-crop of the same PDF does not already give for free.
    """
    affected = _fetch_affected(conn, limit)

    by_top: dict[str, dict] = {}
    for row in affected:
        by_top.setdefault(str(row["top_id"]), {"meta": row, "leaves": []})
        by_top[str(row["top_id"])]["leaves"].append(row)

    out: list[ParentToRead] = []
    with conn.cursor() as cur:
        for top_id, group in by_top.items():
            meta = group["meta"]
            cur.execute(
                "select page_number, bbox from question_assets"
                " where question_id = %s and kind = 'question_crop' order by sort_order",
                (top_id,),
            )
            asset_rows = cur.fetchall()

            qp_name = caie_filename(
                meta["syllabus_code"], meta["year"], meta["season"], "qp",
                meta["component"], meta["variant"],
            )
            qp_path = papers_dir / meta["syllabus_code"] / qp_name

            crop_paths: list[str] = []
            if not asset_rows:
                log.warning("top-level question %s has no crops at all", top_id)
            elif qp_path.is_file():
                for i, asset in enumerate(asset_rows):
                    out_path = work_dir / "marks-worksheet" / top_id / f"page-{i}.png"
                    try:
                        crop(qp_path, asset["page_number"], tuple(asset["bbox"]), out_path)
                        crop_paths.append(str(out_path))
                    except Exception:
                        log.exception("crop failed for parent %s page %s", top_id, i)
            else:
                log.warning("no source PDF for %s (expected %s)", qp_name, qp_path)

            out.append(
                ParentToRead(
                    parent_id=top_id,
                    paper_slug=meta["paper_slug"],
                    display_label=meta["top_display_label"],
                    crop_paths=crop_paths,
                    leaves=[
                        LeafToRead(
                            leaf_id=str(leaf["leaf_id"]),
                            display_label=leaf["display_label"],
                            mark_scheme_text=leaf["mark_scheme_text"],
                        )
                        for leaf in group["leaves"]
                    ],
                )
            )

    return out


def write_worksheet(parents: list[ParentToRead], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([asdict(p) for p in parents], indent=2, ensure_ascii=False), "utf-8"
    )


@dataclass
class ApplyReport:
    updated: int = 0
    unknown_leaf_ids: list[str] = field(default_factory=list)
    invalid_marks: list[str] = field(default_factory=list)


def apply_marks_decisions(conn: psycopg.Connection, decisions: list[dict]) -> ApplyReport:
    report = ApplyReport()
    with conn.cursor() as cur:
        for decision in decisions:
            leaf_id = decision.get("leaf_id")
            marks = decision.get("max_marks")
            if not isinstance(marks, int) or marks <= 0:
                report.invalid_marks.append(str(leaf_id))
                continue
            cur.execute(
                "update questions set max_marks = %s"
                " where id = %s and question_type = 'structured' and max_marks is null",
                (marks, leaf_id),
            )
            if cur.rowcount == 0:
                report.unknown_leaf_ids.append(str(leaf_id))
            else:
                report.updated += 1
    return report
