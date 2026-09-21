"""DeepSeek's API — the second provider alongside Gemini for the two stages
that call a model directly (`extract.py`, `tagging.py`, plus
`tagging.tag_from_crop` for automated tag verification).

The contract, checked against DeepSeek's own documentation
(api-docs.deepseek.com) rather than assumed, because it differs from the
Anthropic-Messages client this file replaced in every detail that matters:

  * `POST https://api.deepseek.com/chat/completions`, OpenAI format, with
    `Authorization: Bearer <key>` — not `x-api-key`.
  * The system prompt is the first message, not a top-level field, and the
    reply's text is `choices[0].message.content`, a plain string.
  * Images are `image_url` parts carrying a base64 data URL, and are accepted
    **only in user messages** (a `400` in a system or assistant message) and
    only by `deepseek-flash`. `deepseek-v4-pro` is text-only, so this client
    refuses to send it an image rather than let the request fail at the API.
  * `response_format={"type": "json_object"}` is a real JSON mode, but it
    requires the word "json" and an example shape in the prompt, and the docs
    warn it can occasionally return *empty* content — so an empty reply is
    treated as a transient failure and retried, not as an answer.
  * **Thinking mode is on by default**, at high effort, and its reasoning
    tokens are drawn from the same `max_tokens` budget as the answer. Left at
    the default, a 1,024-token classification call spends its whole budget
    reasoning and returns nothing. This client therefore states the mode
    explicitly on every call (off unless `settings.deepseek_thinking`) and,
    when thinking is on, raises the budget to fit it.
  * Status codes: 429 (concurrency limit), 500 and 503 are worth retrying; 401
    (bad key) and 402 (out of balance) are not, and — unlike a malformed
    reply — mean every further call would fail identically. They raise
    `DeepSeekAccountError` so a caller that otherwise carries on past one bad
    question (`verify.py`) knows to stop instead.

Deliberately a raw `httpx` POST rather than the `openai` package: this
codebase already makes the same kind of call for Voyage (`embed.py`) without
pulling in an SDK for a handful of JSON requests.

Cost note: DeepSeek caches prompts automatically by shared *prefix*, at a
fraction of the uncached input price. Callers therefore keep the stable
material (system prompt, then the syllabus) ahead of anything that varies per
call — see `tagging.tag_from_crop`, where the image deliberately comes after
the syllabus for exactly that reason.
"""

from __future__ import annotations

import base64
import logging
import re
import time

import httpx

from .config import settings

log = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Models the docs state cannot read an image. Sending one an image is a
# programming error worth catching before a network call, not after.
_TEXT_ONLY_MODELS = frozenset({"deepseek-v4-pro"})

# Reasoning tokens count against max_tokens, so a thinking call needs room for
# the reasoning *and* the answer. The API's own thinking-mode default is 64K;
# this is the floor for a structured-output call, generous for a topic
# classification and still a small fraction of what the API would allow.
_THINKING_MIN_MAX_TOKENS = 16000

# A transient failure on the first page of a long backfill burned that whole
# paper's progress once, since nothing commits until a paper finishes — so a
# handful of retries with exponential backoff (what DeepSeek recommends for a
# 429) is what this is for. 3, 6, 12, 24, 48 seconds: about 90s in total.
_MAX_ATTEMPTS = 6
_RETRY_BASE_DELAY_S = 3.0
_RETRY_MAX_DELAY_S = 60.0

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class DeepSeekError(RuntimeError):
    """A call that failed in a way another attempt at the same request will
    not fix: a rejected request, a reply cut off by `max_tokens`, or a reply
    that stayed empty through every retry."""


class DeepSeekAccountError(DeepSeekError):
    """A 401 or 402 — a wrong key, or no balance. Everything else this
    account sends will fail the same way, so callers that would otherwise
    skip past one failed item must let this one propagate."""


class _BadReply(Exception):
    """A 200 whose body cannot be used — empty content (which the docs say
    happens occasionally) or a body that is not JSON. Retryable, unlike a
    reply truncated by `max_tokens`, which would truncate again."""


def _strip_json_fence(text: str) -> str:
    """JSON mode makes a fenced reply unusual, but it is a request to the
    model, not decoding the API enforces, and the prompt's own "no markdown
    fence" is likewise a request. Cheap insurance."""
    match = _JSON_FENCE_RE.search(text)
    return match.group(1) if match else text.strip()


def _fix_trailing_commas(text: str) -> str:
    """`{"a": 1, }` — a comma directly before a closing brace or bracket is
    never valid JSON, so removing one can only repair a malformed reply, never
    alter a well-formed one."""
    return _TRAILING_COMMA_RE.sub(r"\1", text)


def text_content_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_content_block(png_bytes: bytes) -> dict:
    """An OpenAI-format image part: a base64 `data:` URL, not the separate
    `source` object Anthropic's format uses."""
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "auto"},
    }


def _has_image(user_content: str | list[dict]) -> bool:
    return isinstance(user_content, list) and any(
        block.get("type") == "image_url" for block in user_content
    )


def _mentions_json(system: str, user_content: str | list[dict]) -> bool:
    parts = [system]
    if isinstance(user_content, str):
        parts.append(user_content)
    else:
        parts.extend(block.get("text", "") for block in user_content if block.get("type") == "text")
    return "json" in " ".join(parts).lower()


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUSES
    if isinstance(exc, _BadReply):
        return True
    # Every other transport-level failure httpx can raise — a timeout, a
    # dropped connection, "server disconnected without sending a response"
    # (httpx.RemoteProtocolError) — is what a flaky upstream produces and a
    # retry fixes. A bare RemoteProtocolError once propagated straight past a
    # narrower isinstance check, in the first real backfill, without a retry.
    return isinstance(exc, httpx.TransportError)


def _retry_delay(attempt: int, exc: Exception) -> float:
    delay = min(_RETRY_MAX_DELAY_S, _RETRY_BASE_DELAY_S * 2 ** (attempt - 1))
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            delay = max(delay, min(float(retry_after), 120.0))
    return delay


def _reply_text(response: httpx.Response) -> str:
    """The text of a 200 reply, or the reason it is unusable."""
    try:
        payload = response.json()
        choice = payload["choices"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise _BadReply(f"response was not a chat completion: {exc!r}") from exc

    text = (choice.get("message") or {}).get("content") or ""
    if text.strip():
        return text

    if choice.get("finish_reason") == "length":
        # Not retried: the same request would spend the same budget and be cut
        # off in the same place. With thinking on this usually means the
        # reasoning alone used the whole allowance.
        raise DeepSeekError(
            "reply was cut off by max_tokens before any answer was produced — "
            "raise the token budget, or turn thinking off "
            "(NOTEACADEMY_DEEPSEEK_THINKING)"
        )
    raise _BadReply(f"empty content (finish_reason={choice.get('finish_reason')!r})")


def chat_json(
    *,
    model: str,
    system: str,
    user_content: str | list[dict],
    max_tokens: int = 4096,
    thinking: bool | None = None,
    client: httpx.Client | None = None,
) -> str:
    """One chat-completion call in JSON mode, returning the JSON text of the
    reply (fence stripped and trailing commas repaired, otherwise
    unvalidated — that is the caller's job).

    Retries a transient failure (429/5xx, a timeout, a dropped connection, an
    empty reply) with exponential backoff, honouring `Retry-After`. Anything
    else fails at once, since retrying a request that is wrong only ever
    produces the same wrong answer.

    `thinking` overrides `settings.deepseek_thinking` for this one call: None
    follows the setting, True or False forces it. A verification run turns it
    on (it changed two of eight borderline verdicts, both correctly, for about
    a tenth of a cent a call) without making every other stage pay for it.
    """
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    if _has_image(user_content) and model in _TEXT_ONLY_MODELS:
        raise ValueError(
            f"{model} does not accept images — use a vision model such as "
            "deepseek-flash (NOTEACADEMY_DEEPSEEK_VISION_MODEL)"
        )

    thinking = settings.deepseek_thinking if thinking is None else thinking
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max(max_tokens, _THINKING_MIN_MAX_TOKENS) if thinking else max_tokens,
        "response_format": {"type": "json_object"},
        # Stated either way: the API's own default is thinking *on*.
        "thinking": {"type": "enabled" if thinking else "disabled"},
        "stream": False,
    }
    if not thinking:
        # Sampling parameters are ignored in thinking mode. Out of it, the
        # default temperature (1) is wrong for extraction and classification:
        # the same page should come back the same way twice.
        body["temperature"] = 0

    if not _mentions_json(system, user_content):
        # JSON mode wants the word "json" in the prompt or it may return
        # nothing. Every prompt in this codebase spells out its shape already;
        # this is the guard for the next one that forgets.
        body["messages"][0]["content"] = f"{system}\n\nRespond with a single JSON object."

    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    owned = client is None
    # The server holds a request open with empty lines while it queues (for up
    # to ten minutes before inference starts); those bytes reset httpx's read
    # timeout, so a generous read allowance costs nothing on a healthy call.
    client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = client.post(DEEPSEEK_URL, headers=headers, json=body)
                if response.status_code in (401, 402):
                    raise DeepSeekAccountError(
                        "DeepSeek rejected the API key (401)"
                        if response.status_code == 401
                        else "DeepSeek account is out of balance (402)"
                    )
                if response.status_code in (400, 422):
                    # The reason is in the body ("Images in system messages
                    # return a 400"), and raise_for_status would drop it.
                    raise DeepSeekError(
                        f"DeepSeek rejected the request ({response.status_code}): "
                        f"{response.text[:300].strip()}"
                    )
                response.raise_for_status()
                text = _reply_text(response)
                break
            except (httpx.HTTPStatusError, httpx.TransportError, _BadReply) as exc:
                if not _is_retryable(exc):
                    raise
                if attempt == _MAX_ATTEMPTS:
                    if isinstance(exc, _BadReply):
                        raise DeepSeekError(
                            f"no usable reply after {_MAX_ATTEMPTS} attempts: {exc}"
                        ) from exc
                    raise
                delay = _retry_delay(attempt, exc)
                log.warning(
                    "deepseek call failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt, _MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)
    finally:
        if owned:
            client.close()

    return _fix_trailing_commas(_strip_json_fence(text))
