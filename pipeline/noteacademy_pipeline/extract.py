"""Vision extraction: a rendered page in, structured questions out.

Why a vision model rather than a layout-analysis stack (LayoutLM, Detectron,
hand-tuned heuristics): CAIE papers vary in layout across 25 years and 40
subjects, and the classical pipeline needs retraining or retuning for each
variation. A vision model reads all of them, and the structured-output schema
means it cannot return a shape we fail to parse.

What it does *not* solve is being wrong. Everything here writes
`extraction_status='extracted'`, which RLS keeps invisible to students, and the
cross-check against the PDF text layer flags pages where the model and the text
disagree so a human sees them first.
"""

from __future__ import annotations

import logging
import re

from google import genai
from google.genai import types

from .config import settings
from .render import RenderedPage
from .schemas import ExtractedPage, MarkSchemeGrid

log = logging.getLogger(__name__)

# No explicit prompt caching here: Gemini's is a separate CachedContent object
# with its own minimum-token threshold and TTL, not a per-block flag like the
# provider this replaced, and the system prompt below is well under that
# threshold. Revisit if a large shared prefix (e.g. a syllabus block) ever
# needs one.
EXTRACTION_SYSTEM = """You extract exam questions from Cambridge (CAIE) past papers.

You are given one rendered page and, when available, the text layer beneath it.
Return every question that begins or continues on this page.

Rules that matter:

- Labels exactly as printed. '7(a)(ii)', not '7a ii' and not '7.a.ii'.
- A question split by a page break is returned on both pages, with
  `continues_on_next_page` / `is_continuation` set. The loader stitches them.
- Bounding boxes are in PDF points with the origin at the TOP-LEFT of the page,
  and must enclose the figures belonging to the question, not just its text.
- Mathematics as LaTeX. Chemistry as written.
- Never invent a question that is not on the page. Covers, blank pages, formula
  sheets and instruction pages have `is_content_page: false` and no questions.
  Returning nothing is correct and expected for those pages.
- If the text layer contradicts what you see, trust what you see, but do not
  smooth over the difference — return what is printed.
"""

MCQ_MS_SYSTEM = """You read the answer grid from a Cambridge multiple-choice mark scheme.

These are tables of question number to letter. Return every entry. Do not infer
or fill gaps: if a number is unreadable, omit it rather than guessing. An
incorrect answer key is worse than a missing one, because students trust it.
"""

# Question labels as CAIE prints them: 1, 1(a), 1(a)(ii), 1(b)(iii).
LABEL_RE = re.compile(r"^\s*(\d{1,2})\s*(\([a-z]\))?\s*(\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\))?")


def _client() -> genai.Client:
    return genai.Client(api_key=settings.google_api_key or None)


def extract_page(page: RenderedPage, *, client: genai.Client | None = None) -> ExtractedPage:
    """Extract the questions on a single rendered page."""
    client = client or _client()

    content: list = [page.as_image_part()]
    if page.has_text_layer:
        content.append(
            f"Text layer for page {page.page_number} "
            f"(page is {page.width_pt:.0f}x{page.height_pt:.0f} pt):\n\n"
            f"{page.text}"
        )
    else:
        content.append(
            f"Page {page.page_number} has no text layer (scanned). "
            f"Page is {page.width_pt:.0f}x{page.height_pt:.0f} pt."
        )

    response = client.models.generate_content(
        model=settings.extraction_model,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACTION_SYSTEM,
            response_mime_type="application/json",
            response_schema=ExtractedPage,
            max_output_tokens=16000,
            # This is the step the "wrong place to economise" comment on
            # extraction_model is about — full thinking, not the default.
            thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
        ),
    )

    result = response.parsed
    result.page_number = page.page_number       # never trust the model for this
    return result


def extract_mcq_answers(
    pages: list[RenderedPage], *, client: genai.Client | None = None
) -> dict[str, str]:
    """Read the answer grid out of an MCQ mark scheme.

    This is the cheapest high-value step in the whole pipeline: an MCQ mark
    scheme is a table, so the practice arena can be populated with a trustworthy
    answer key long before structured-paper segmentation is reliable.
    """
    client = client or _client()
    answers: dict[str, str] = {}

    for page in pages:
        response = client.models.generate_content(
            model=settings.extraction_model,
            contents=[page.as_image_part(), page.text if page.has_text_layer else ""],
            config=types.GenerateContentConfig(
                system_instruction=MCQ_MS_SYSTEM,
                response_mime_type="application/json",
                response_schema=MarkSchemeGrid,
                max_output_tokens=8000,
            ),
        )
        for number, option in response.parsed.answers.items():
            key = number.strip()
            if key in answers and answers[key] != option:
                log.warning(
                    "conflicting answer for question %s: %s vs %s — flagged for review",
                    key,
                    answers[key],
                    option,
                )
                continue
            answers[key] = option

    return answers


def cross_check(page: RenderedPage, extracted: ExtractedPage) -> list[str]:
    """Compare the model's output against the PDF text layer.

    Cheap, deterministic, and catches the failure that matters most: a question
    label the model produced that does not appear on the page at all. Anything
    returned here should route the page to human review rather than straight into
    the bank.
    """
    if not page.has_text_layer:
        return []

    problems: list[str] = []
    haystack = re.sub(r"\s+", "", page.text)

    for question in extracted.questions:
        if question.is_continuation:
            continue
        match = LABEL_RE.match(question.display_label)
        if not match:
            problems.append(f"unparseable label {question.display_label!r}")
            continue
        number = match.group(1)
        if number not in re.findall(r"\d{1,2}", haystack):
            problems.append(
                f"label {question.display_label!r} has no matching number in the text layer"
            )

    for question in extracted.questions:
        if question.bbox.x1 <= question.bbox.x0 or question.bbox.y1 <= question.bbox.y0:
            problems.append(f"degenerate bbox on {question.display_label!r}")
        if question.bbox.x1 > page.width_pt + 1 or question.bbox.y1 > page.height_pt + 1:
            problems.append(f"bbox on {question.display_label!r} falls outside the page")

    return problems
