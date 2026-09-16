"""Qwen models via ModelScope's OpenAI-compatible API-Inference endpoint.

A second provider alongside Gemini for the two stages that call a model
directly (`extract.py`, `tagging.py`), added after a persistent Gemini `503`
left real ModelScope/Qwen credits sitting unused. Deliberately a raw `httpx`
POST rather than the `openai` package — this codebase already makes that call
for Voyage (`embed.py`) rather than pull in an SDK for a handful of JSON
requests, and ModelScope's endpoint is plain REST underneath its
OpenAI-compatible surface.

The one thing this endpoint does not give us that Gemini's `response_schema`
does: constrained decoding. `response_format: json_object` is requested, but
nothing here guarantees the model can't wrap its answer in a markdown fence
or add a stray sentence, so every caller still validates the result against
its own Pydantic schema and gets a real parse error, not a silent bad write,
if a response doesn't match.
"""

from __future__ import annotations

import base64
import logging
import re

import httpx

from .config import settings

log = logging.getLogger(__name__)

MODELSCOPE_URL = "https://api-inference.modelscope.cn/v1/chat/completions"

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL)


def _strip_json_fence(text: str) -> str:
    """Qwen routinely wraps a JSON answer in a ```json fence even when asked
    not to — Gemini's `response_schema` never needed this because it
    constrains the output tokens directly, a guarantee this endpoint's plain
    `response_format` does not make."""
    match = _JSON_FENCE_RE.search(text)
    return match.group(1) if match else text.strip()


def text_content_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_content_block(png_bytes: bytes) -> dict:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}


def chat_json(
    *,
    model: str,
    system: str,
    user_content: str | list[dict],
    client: httpx.Client | None = None,
) -> str:
    """One chat completion, returning the JSON text of the reply (fence
    stripped, otherwise unvalidated — that's the caller's job)."""
    if not settings.modelscope_api_key:
        raise RuntimeError("MODELSCOPE_API_KEY is not set")

    owned = client is None
    client = client or httpx.Client(timeout=180.0)
    try:
        response = client.post(
            MODELSCOPE_URL,
            headers={"Authorization": f"Bearer {settings.modelscope_api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owned:
            client.close()

    content = payload["choices"][0]["message"]["content"]
    return _strip_json_fence(content)
