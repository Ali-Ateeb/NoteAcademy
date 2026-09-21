"""Topic tagging against a closed set of syllabus learning outcomes.

Tagging accuracy *is* the product. A topical question bank whose tags are 80%
right is worse than no topical bank at all, because a student revising
Kinematics gets handed a Thermal Physics question and stops trusting the site.

Two rules make this tractable:

1. The model chooses from the actual syllabus outcomes, supplied as a closed
   list. It never invents a topic code. Anything not in the list is rejected by
   the loader rather than written to the database.
2. Every assignment carries a confidence. Below the floor it goes to a human
   review queue instead of to students. That queue is not optional tooling — it
   is how the bank stays correct.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types

from .config import settings
from .schemas import TopicAssignment, TopicTagging

log = logging.getLogger(__name__)

TAGGING_SYSTEM = """You assign Cambridge (CAIE) exam questions to syllabus topics.

You are given the syllabus topics for one subject, each with its code and its
published learning objectives, and one exam question.

- Choose the ONE topic whose learning objectives the question principally tests.
  That is the primary topic.
- Add a secondary topic only where the question genuinely cannot be answered
  without a second area of the syllabus. Most questions have none. A question
  tagged with four topics is useless for revision — padding the list actively
  harms the student.
- Use only topic codes from the supplied list. If nothing fits, choose the
  closest and give it a low confidence; do not invent a code.
- Confidence is your actual certainty. A question you are unsure about is sent to
  a human, which is the correct outcome — an overconfident wrong tag is not.
"""


@dataclass
class TopicOption:
    code: str
    title: str
    learning_objectives: list[str]

    def render(self) -> str:
        objectives = "\n".join(f"    - {o}" for o in self.learning_objectives)
        return f"  {self.code} — {self.title}\n{objectives}" if objectives else \
               f"  {self.code} — {self.title}"


def render_syllabus(topics: list[TopicOption]) -> str:
    return "\n".join(topic.render() for topic in topics)


def create_topic_cache(
    topics: list[TopicOption],
    *,
    client: genai.Client | None = None,
    ttl_seconds: int = 3600,
) -> str | None:
    """Cache the syllabus block once per subject rather than once per question.

    The syllabus is identical for every question `tag_question` is called on
    within a subject — this is the block that comment used to say was "worth
    adding here if a full-subject backfill's cost ever demands it". Gemini
    prompt caching is an explicit `CachedContent` resource rather than a
    per-block flag, and it has its own minimum-token floor: a subject with few
    topics or short learning objectives can render a syllabus block too small
    to be worth caching, and the API refuses to create one for it rather than
    silently caching nothing. That refusal is not this function's problem to
    solve — returning `None` lets `tag_question` fall back to sending the
    syllabus inline, which is correct either way, just not the cheaper path.

    Call once per subject, pass the result to every `tag_question` call for
    that subject's questions, and let it expire — `ttl_seconds` should outlast
    one subject's backfill, not survive between separate runs.
    """
    client = client or genai.Client(api_key=settings.google_api_key or None)

    try:
        cache = client.caches.create(
            model=settings.extraction_model,
            config=types.CreateCachedContentConfig(
                system_instruction=TAGGING_SYSTEM,
                contents=[f"Syllabus topics:\n\n{render_syllabus(topics)}"],
                ttl=f"{ttl_seconds}s",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - caching is an optimisation, never a requirement
        log.info("not caching the syllabus block (%s); tagging inline instead", exc)
        return None

    return cache.name


def tag_question(
    question_text: str,
    topics: list[TopicOption],
    *,
    mark_scheme: str | None = None,
    client: genai.Client | None = None,
    cached_content: str | None = None,
) -> TopicTagging:
    """Assign a question to syllabus topics.

    The mark scheme is included when available: what a question is *really*
    testing is often clearer from what earns the marks than from the prompt. A
    question that reads like recall but whose mark scheme awards marks for a
    derivation belongs under the derivation's topic.

    Pass `cached_content` (from `create_topic_cache`) to skip resending the
    syllabus block on every call — the system instruction travels with the
    cache too, since a cached-content request may not also set its own. With
    no cache, this sends the full syllabus inline every time, exactly as
    before. `cached_content` is Gemini-only and ignored under
    `settings.tagging_provider == "deepseek"`, which caches automatically by
    shared prefix instead — the syllabus block leads the prompt for exactly
    that reason.
    """
    if settings.tagging_provider == "deepseek":
        return _tag_question_deepseek(question_text, topics, mark_scheme=mark_scheme)
    return _tag_question_gemini(
        question_text, topics, mark_scheme=mark_scheme, client=client, cached_content=cached_content
    )


def _tag_question_gemini(
    question_text: str,
    topics: list[TopicOption],
    *,
    mark_scheme: str | None = None,
    client: genai.Client | None = None,
    cached_content: str | None = None,
) -> TopicTagging:
    client = client or genai.Client(api_key=settings.google_api_key or None)

    blocks = [f"Question:\n\n{question_text}"]
    if mark_scheme:
        blocks.append(f"Mark scheme:\n\n{mark_scheme}")

    config_kwargs: dict = {
        "response_mime_type": "application/json",
        "response_schema": TopicTagging,
        "max_output_tokens": 4000,
        "thinking_config": types.ThinkingConfig(thinking_level="HIGH"),
    }
    if cached_content is not None:
        config_kwargs["cached_content"] = cached_content
    else:
        config_kwargs["system_instruction"] = TAGGING_SYSTEM
        blocks.insert(0, f"Syllabus topics:\n\n{render_syllabus(topics)}")

    response = client.models.generate_content(
        model=settings.extraction_model,
        contents=blocks,
        config=types.GenerateContentConfig(**config_kwargs),
    )

    return _validated(response.parsed, topics)


# The exact shape _tag_question_deepseek asks for — spelled out in the prompt
# because, unlike Gemini's response_schema, JSON mode guarantees valid JSON
# but not a particular schema (and requires an example in the prompt).
_TAGGING_JSON_SHAPE = (
    'Respond with ONLY a JSON object of this exact shape, no other text, '
    "no markdown fence:\n"
    '{"primary": {"topic_code": "...", "confidence": 0.0-1.0, "reasoning": "..."}, '
    '"secondary": [{"topic_code": "...", "confidence": 0.0-1.0, "reasoning": "..."}]}'
)


def _tag_question_deepseek(
    question_text: str,
    topics: list[TopicOption],
    *,
    mark_scheme: str | None = None,
) -> TopicTagging:
    from .deepseek import chat_json

    blocks = [
        f"Syllabus topics:\n\n{render_syllabus(topics)}",
        f"Question:\n\n{question_text}",
    ]
    if mark_scheme:
        blocks.append(f"Mark scheme:\n\n{mark_scheme}")
    blocks.append(_TAGGING_JSON_SHAPE)

    raw = chat_json(
        model=settings.deepseek_text_model,
        system=TAGGING_SYSTEM,
        user_content="\n\n".join(blocks),
    )
    return _validated(TopicTagging.model_validate_json(raw), topics)


def _validated(tagging: TopicTagging, topics: list[TopicOption]) -> TopicTagging:
    valid = {topic.code for topic in topics}

    if tagging.primary.topic_code not in valid:
        log.warning(
            "model returned unknown topic code %r; forcing to review",
            tagging.primary.topic_code,
        )
        tagging.primary.confidence = 0.0

    tagging.secondary = [t for t in tagging.secondary if t.topic_code in valid]
    return tagging


def needs_review(tagging: TopicTagging) -> bool:
    return tagging.primary.confidence < settings.tag_confidence_floor


VERIFY_FROM_CROP_SYSTEM = """You are shown a Cambridge (CAIE) exam question exactly as it was
printed -- the image is what a student sees, options included -- and the syllabus topics for
its subject.

Choose the ONE topic whose learning objectives this question principally tests. Use only topic
codes from the supplied list. If nothing fits, choose the closest and give it a low confidence;
never invent a code."""

_VERIFY_JSON_SHAPE = (
    "Respond with ONLY a JSON object of this exact shape, no other text, "
    "no markdown fence:\n"
    '{"topic_code": "...", "confidence": 0.0-1.0, "reasoning": "..."}'
)


def tag_from_crop(crop_path: Path, topics: list[TopicOption]) -> TopicAssignment:
    """A second, independent read of one question from its printed crop
    rather than its extracted text — the automated counterpart to the manual
    second pass `verify.py`'s contact sheets exist for.

    This is the point a text-only second opinion (`tag_question` again,
    against the same PDF-text-layer extraction the first pass already used)
    would miss: a crop is a genuinely different signal, not a second
    classifier reading the same scrambled words. DeepSeek-only —
    there is no Gemini path here, because the existing human/Claude-session
    workflow (`tag-verify-export`/`tag-verify-apply`) already covers that
    case at zero API cost. This exists for when a model is doing the
    looking instead of a person.
    """
    from .deepseek import chat_json, image_content_block, text_content_block

    raw = chat_json(
        model=settings.deepseek_vision_model,
        system=VERIFY_FROM_CROP_SYSTEM,
        max_tokens=1024,
        # The syllabus leads and the image follows, on purpose: DeepSeek caches
        # by shared prefix, and the syllabus is identical on every call while
        # the crop never is. Image first would put the one thing that always
        # differs ahead of the one thing that never does, and nothing after it
        # could ever be a cache hit.
        user_content=[
            text_content_block(f"Syllabus topics:\n\n{render_syllabus(topics)}"),
            image_content_block(crop_path.read_bytes()),
            text_content_block(_VERIFY_JSON_SHAPE),
        ],
    )
    assignment = TopicAssignment.model_validate_json(raw)

    if assignment.topic_code not in {t.code for t in topics}:
        log.warning(
            "model returned unknown topic code %r; forcing to review",
            assignment.topic_code,
        )
        assignment.confidence = 0.0

    return assignment
