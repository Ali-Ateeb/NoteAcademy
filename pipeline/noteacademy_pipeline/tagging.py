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

import anthropic

from .config import settings
from .schemas import TopicTagging

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


def tag_question(
    question_text: str,
    topics: list[TopicOption],
    *,
    mark_scheme: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> TopicTagging:
    """Assign a question to syllabus topics.

    The mark scheme is included when available: what a question is *really*
    testing is often clearer from what earns the marks than from the prompt. A
    question that reads like recall but whose mark scheme awards marks for a
    derivation belongs under the derivation's topic.
    """
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)

    # The syllabus is identical for every question in a subject, so it sits in
    # the cached prefix and is billed once per subject rather than once per
    # question. Across a full backfill that is the majority of the input tokens.
    syllabus_block = {
        "type": "text",
        "text": f"Syllabus topics:\n\n{render_syllabus(topics)}",
        "cache_control": {"type": "ephemeral"},
    }

    question_block = {"type": "text", "text": f"Question:\n\n{question_text}"}
    blocks = [syllabus_block, question_block]
    if mark_scheme:
        blocks.append({"type": "text", "text": f"Mark scheme:\n\n{mark_scheme}"})

    response = client.messages.parse(
        model=settings.extraction_model,
        max_tokens=4000,
        system=TAGGING_SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": blocks}],
        output_format=TopicTagging,
    )

    tagging = response.parsed_output
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
