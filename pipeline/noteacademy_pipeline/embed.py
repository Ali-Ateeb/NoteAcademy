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

import httpx

from .config import settings

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
BATCH_SIZE = 128


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            response = client.post(
                VOYAGE_URL,
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={
                    "input": batch,
                    "model": settings.embedding_model,
                    "input_type": input_type,
                },
            )
            response.raise_for_status()
            payload = response.json()
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
