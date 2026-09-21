"""Embeddings for the retrieval index.

Anthropic does not serve an embeddings endpoint; Voyage is the provider
Anthropic's own documentation points at. voyage-3 is 1024-dimensional, which is
what migration 0007 declares. Changing the model means changing that column and
re-embedding the corpus — you cannot mix dimensionalities in one index.

Content is hashed before embedding so a re-run only pays for questions whose text
actually changed.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
import pymupdf

from .config import settings
from .naming import caie_filename
from .worksheet import question_text as crop_text

log = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
BATCH_SIZE = 128

# A free/new-account rate limit hits on roughly the second batch of any real
# backfill, confirmed live — deepseek.py needed the identical fix for the
# identical reason, so it gets the identical shape here. Reactive retry alone
# was not enough: one real run rescued its second batch after four backed-off
# retries, then exhausted all five on the very next one — the limit here is
# strict enough that a fixed pace between batches, not just a reaction after
# the fact, is needed to actually stay under it.
_MAX_ATTEMPTS = 6
_RETRY_BASE_DELAY_S = 8.0
_INTER_BATCH_DELAY_S = 20.0


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _post_with_retry(client: httpx.Client, batch: list[str], input_type: str) -> dict:
    """One Voyage call, retried on a rate limit or a transient server/network
    failure. Honours a `Retry-After` header on a 429 when Voyage sends one,
    since that is a more accurate wait than a guessed backoff; falls back to
    linear backoff otherwise.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.post(
                VOYAGE_URL,
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={"input": batch, "model": settings.embedding_model, "input_type": input_type},
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            retryable = isinstance(exc, httpx.TransportError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code in (429, 500, 502, 503, 504)
            )
            if attempt == _MAX_ATTEMPTS or not retryable:
                raise

            delay = _RETRY_BASE_DELAY_S * attempt
            if isinstance(exc, httpx.HTTPStatusError):
                retry_after = exc.response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)

            log.warning(
                "voyage call failed (attempt %d/%d): %s — retrying in %.0fs",
                attempt, _MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)

    raise AssertionError("unreachable")  # loop always returns or raises


def embed_texts(
    texts: list[str],
    *,
    input_type: str = "document",
    client: httpx.Client | None = None,
) -> list[list[float]]:
    """Embed a list of texts, batched.

    `input_type` matters: index the corpus as 'document' and embed the student's
    pasted question as 'query'. Voyage encodes them differently, and mixing the
    two measurably degrades match quality — which in this system means the solver
    fails to recognise a question it actually has the mark scheme for.
    """
    if not texts:
        return []
    if not settings.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY is not set")

    owned = client is None
    client = client or httpx.Client(timeout=60.0)
    vectors: list[list[float]] = []

    try:
        starts = range(0, len(texts), BATCH_SIZE)
        for i, start in enumerate(starts):
            if i > 0:
                # Paced, not reactive: waiting only after a 429 already
                # happened wasn't enough to stay under a strict per-minute
                # limit (see _MAX_ATTEMPTS's comment) — this keeps every
                # batch's own retries from starting behind before they begin.
                time.sleep(_INTER_BATCH_DELAY_S)
            batch = texts[start : start + BATCH_SIZE]
            payload = _post_with_retry(client, batch, input_type)
            # Voyage returns results in request order, but the index is present
            # and authoritative — sort by it rather than trusting position.
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors.extend(item["embedding"] for item in ordered)
    finally:
        if owned:
            client.close()

    return vectors


def embed_query(text: str, *, client: httpx.Client | None = None) -> list[float]:
    return embed_texts([text], input_type="query", client=client)[0]


def to_pgvector(vector: list[float]) -> str:
    """pgvector's text input format."""
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


@dataclass
class EmbedCandidate:
    id: str
    canonical_id: str
    text: str


@dataclass
class EmbedReport:
    subject: str
    candidates: int = 0
    groups: int = 0
    api_calls: int = 0
    written: int = 0
    reused_from_duplicate: int = 0
    unchanged: int = 0
    missing_text: list[str] = field(default_factory=list)


def _mcq_candidates(
    conn: psycopg.Connection, subject_slug: str, papers_dir: Path
) -> list[EmbedCandidate]:
    """Approved MCQs, with text from `question_text`/`question_options` where a
    prior `mcq-options` backfill has run, or read fresh from the paper's own
    text layer otherwise — exactly the fallback `worksheet.py`'s tagging pass
    already relies on, since as of this pipeline's current state that backfill
    has not been run at all and it is the only text most MCQs have.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.id, q.canonical_question_id, q.question_text,
                   p.slug as paper_slug, p.component, p.variant,
                   es.year, es.season, sub.syllabus_code,
                   qa.page_number, qa.bbox
              from questions q
              join papers p on p.id = q.paper_id
              join subjects sub on sub.id = p.subject_id
              join exam_sessions es on es.id = p.exam_session_id
              left join question_assets qa
                on qa.question_id = q.id and qa.kind = 'question_crop'
             where sub.slug = %s
               and q.question_type = 'mcq'
               and q.extraction_status = 'approved'
             order by es.year desc, p.slug, q.ordinal
            """,
            (subject_slug,),
        )
        rows = cur.fetchall()

    ids = [row["id"] for row in rows]
    options_by_question: dict[str, list[tuple[str, str]]] = {}
    if ids:
        with conn.cursor() as cur:
            cur.execute(
                "select question_id, option, content from question_options"
                " where question_id = any(%s) order by question_id, option",
                (ids,),
            )
            for row in cur.fetchall():
                options_by_question.setdefault(row["question_id"], []).append(
                    (row["option"], row["content"])
                )

    by_paper: dict[str, list[dict]] = {}
    for row in rows:
        by_paper.setdefault(row["paper_slug"], []).append(row)

    candidates: list[EmbedCandidate] = []
    for questions in by_paper.values():
        first = questions[0]
        needs_pdf = any(
            not q["question_text"] and q["bbox"] and q["page_number"] for q in questions
        )
        doc = None
        if needs_pdf:
            name = caie_filename(
                first["syllabus_code"], first["year"], first["season"], "qp",
                first["component"], first["variant"],
            )
            path = papers_dir / first["syllabus_code"] / name
            if path.is_file():
                doc = pymupdf.open(path)

        try:
            for row in questions:
                stem = (row["question_text"] or "").strip()
                if not stem and doc is not None and row["bbox"] and row["page_number"]:
                    stem = crop_text(doc, row["page_number"], row["bbox"]).strip()

                options = [
                    f"{letter}: {content.strip()}"
                    for letter, content in options_by_question.get(row["id"], [])
                    # A described figure ("[Figure: ...]") is not the option's
                    # own words — including it would embed the describer's
                    # phrasing, not anything a student would ever paste.
                    if content and not content.strip().lower().startswith("[figure")
                ]
                text = "\n".join([stem, *options]).strip()

                candidates.append(
                    EmbedCandidate(
                        id=row["id"],
                        canonical_id=row["canonical_question_id"] or row["id"],
                        text=text,
                    )
                )
        finally:
            if doc is not None:
                doc.close()

    return candidates


def _structured_candidates(
    conn: psycopg.Connection, subject_slug: str
) -> list[EmbedCandidate]:
    """Approved structured questions, one candidate per top-level practice
    unit — the same granularity `build_structured_worksheet` tags at, and the
    same text: the top-level stem plus every leaf underneath it, already in
    the database with no PDF to reopen.

    Never deduplicated: `canonical_question_id` is only ever set on MCQs (see
    `verify.group_duplicates`), because a full structured question repeating
    verbatim across paper variants has not been observed the way MCQ items are
    — each is its own candidate.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select q.id, q.display_label, q.question_text
              from questions q
              join papers p on p.id = q.paper_id
              join subjects sub on sub.id = p.subject_id
             where sub.slug = %s
               and q.question_type = 'structured'
               and q.parent_question_id is null
               and q.extraction_status = 'approved'
             order by p.slug, q.ordinal
            """,
            (subject_slug,),
        )
        top_level = cur.fetchall()

    candidates: list[EmbedCandidate] = []
    for row in top_level:
        with conn.cursor() as cur:
            cur.execute(
                """
                select question_text from questions
                 where paper_id = (select paper_id from questions where id = %s)
                   and display_label like %s
                   and question_text is not null and question_text <> ''
                 order by ordinal
                """,
                (row["id"], row["display_label"] + "(%"),
            )
            parts_text = [r["question_text"] for r in cur.fetchall()]

        text = " ".join(
            ([row["question_text"]] if row["question_text"] else []) + parts_text
        ).strip()
        candidates.append(EmbedCandidate(id=row["id"], canonical_id=row["id"], text=text))

    return candidates


def backfill_embeddings(
    conn: psycopg.Connection,
    subject_slug: str,
    papers_dir: Path,
    *,
    dry_run: bool = False,
) -> EmbedReport:
    """Embed every approved question in a subject for the retrieval index.

    Only approved questions: `match_question` (0007_retrieval.sql) never looks
    at anything else, so embedding a question still in the review queue is a
    cost with no possible payoff — the vector could not be returned even if the
    call happened. Verbatim-duplicate MCQs (`canonical_question_id`, from
    `dedupe`) share one vector across the whole group, the same reasoning
    `mcq-options`'s backfill already applies to option text: two paper
    variants printing the same question are not two independent things to pay
    for. Content is hashed per question, so a question whose text has not
    changed since the last run is skipped without spending on it again.
    """
    report = EmbedReport(subject=subject_slug)
    candidates = _mcq_candidates(conn, subject_slug, papers_dir) + _structured_candidates(
        conn, subject_slug
    )
    report.candidates = len(candidates)

    groups: dict[str, list[EmbedCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.canonical_id, []).append(candidate)
    report.groups = len(groups)

    ids = [candidate.id for candidate in candidates]
    existing_hashes: dict[str, str] = {}
    if ids:
        with conn.cursor() as cur:
            cur.execute(
                "select question_id, content_hash from question_embeddings"
                " where question_id = any(%s)",
                (ids,),
            )
            existing_hashes = {row["question_id"]: row["content_hash"] for row in cur.fetchall()}

    to_embed: list[tuple[str, str, list[EmbedCandidate]]] = []
    for members in groups.values():
        text = next((m.text for m in members if m.text), "")
        if not text:
            report.missing_text.extend(m.id for m in members)
            continue

        digest = content_hash(text)
        stale = [m for m in members if existing_hashes.get(m.id) != digest]
        report.unchanged += len(members) - len(stale)
        if stale:
            to_embed.append((digest, text, stale))
            if len(stale) > 1:
                report.reused_from_duplicate += len(stale) - 1

    if not to_embed:
        return report

    vectors = embed_texts([text for _, text, _ in to_embed], input_type="document")
    report.api_calls = len(to_embed)

    if not dry_run:
        with conn.cursor() as cur:
            for (digest, _text, members), vector in zip(to_embed, vectors, strict=True):
                encoded = to_pgvector(vector)
                for member in members:
                    cur.execute(
                        """
                        insert into question_embeddings
                          (question_id, embedding, content_hash, model)
                        values (%s, %s::vector, %s, %s)
                        on conflict (question_id) do update set
                          embedding    = excluded.embedding,
                          content_hash = excluded.content_hash,
                          model        = excluded.model
                        """,
                        (member.id, encoded, digest, settings.embedding_model),
                    )
                    report.written += 1

    return report
