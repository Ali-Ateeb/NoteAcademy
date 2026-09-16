"""Qwen models via ModelScope's API-Inference endpoint at
`api-inference.modelscope.ai` — a second provider alongside Gemini for the
two stages that call a model directly (`extract.py`, `tagging.py`), added
after a persistent Gemini `503` left real ModelScope/Qwen credits sitting
unused.

This endpoint speaks Anthropic's Messages API, not OpenAI's chat completions
— confirmed against ModelScope's own sample, which points the `anthropic`
Python SDK at this `base_url` and calls `client.messages.stream(...)`. That
matters for every detail here: the auth header is `x-api-key`, not
`Authorization: Bearer`; the body is `{model, max_tokens, system, messages}`;
and a reply's text comes back as a list of content blocks, not a single
string. Deliberately a raw `httpx` POST replicating that contract rather than
the `anthropic` package itself — this codebase already makes the same call
for Voyage (`embed.py`) rather than pull in an SDK for a handful of JSON
requests, and the contract is simple enough not to need one.

The one thing this endpoint does not give us that Gemini's `response_schema`
does: constrained decoding. The Messages API has no JSON-mode flag at all, so
every caller spells out the exact JSON shape it wants in the prompt itself
and validates the reply against its own Pydantic schema — a real parse
error on a malformed response, never a silent bad write.
"""

from __future__ import annotations

import base64
import logging
import re
import time

import httpx

from .config import settings

log = logging.getLogger(__name__)

MODELSCOPE_URL = "https://api-inference.modelscope.ai/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Gemini's client (google-genai) retries transiently-failing requests under
# the hood via tenacity; this raw httpx POST has no such thing built in, and
# a lost first attempt was expensive to discover — a persistent-connection
# 504 on the very first page of a 15-paper backfill burned that whole paper's
# progress, since nothing here commits until a paper finishes. A busy
# pre-release "Ambassador" model is exactly the situation a handful of
# retries with backoff is for.
_MAX_ATTEMPTS = 5
_RETRY_BASE_DELAY_S = 3.0

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_json_fence(text: str) -> str:
    """Qwen routinely wraps a JSON answer in a ```json fence even when asked
    not to, and there is no JSON-mode flag on this API to suppress it — the
    prompt's own request for "no markdown fence" is a request, not a
    guarantee."""
    match = _JSON_FENCE_RE.search(text)
    return match.group(1) if match else text.strip()


def _fix_trailing_commas(text: str) -> str:
    """`{"a": 1, }` — Qwen produces this often enough to be worth a fix
    rather than a validation error per occurrence. Safe unconditionally: a
    comma directly before a closing brace or bracket is never valid JSON, so
    removing one can only repair a malformed reply, never alter a
    well-formed one."""
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def text_content_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_content_block(png_bytes: bytes) -> dict:
    """Anthropic's image block shape — base64 data inline with its media
    type, not the `data:` URL OpenAI-compatible endpoints use."""
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": encoded},
    }


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    # Every other transport-level failure httpx can raise — a timeout, a
    # dropped connection, "server disconnected without sending a response"
    # (httpx.RemoteProtocolError) — is exactly the shape of thing a flaky
    # upstream produces and a retry fixes. Caught the hard way: the first
    # real backfill run hit a bare RemoteProtocolError, which isn't a
    # TimeoutException or a ConnectError, and it propagated straight past
    # this function's earlier, narrower isinstance check without a retry.
    return isinstance(exc, httpx.TransportError)


def chat_json(
    *,
    model: str,
    system: str,
    user_content: str | list[dict],
    max_tokens: int = 4096,
    client: httpx.Client | None = None,
) -> str:
    """One Messages API call, returning the JSON text of the reply (fence
    stripped, otherwise unvalidated — that's the caller's job).

    Retries a transient failure (429/5xx, a timeout, a dropped connection)
    up to `_MAX_ATTEMPTS` times with a linear backoff; anything else — a 401,
    a malformed request — fails immediately, since retrying a request that
    is wrong will only ever produce the same wrong answer.
    """
    if not settings.modelscope_api_key:
        raise RuntimeError("MODELSCOPE_API_KEY is not set")

    owned = client is None
    client = client or httpx.Client(timeout=180.0)
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = client.post(
                    MODELSCOPE_URL,
                    headers={
                        "x-api-key": settings.modelscope_api_key,
                        "anthropic-version": ANTHROPIC_VERSION,
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": max_tokens,
                        "system": system,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                )
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if attempt == _MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                delay = _RETRY_BASE_DELAY_S * attempt
                log.warning(
                    "modelscope call failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt, _MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)
    finally:
        if owned:
            client.close()

    # `content` is a list of blocks (text, possibly others); concatenating
    # every text block is the defensive-but-correct read regardless of how
    # many the model happens to return.
    text = "".join(block["text"] for block in payload["content"] if block.get("type") == "text")
    return _fix_trailing_commas(_strip_json_fence(text))
