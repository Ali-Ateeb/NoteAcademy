"""Structured shapes the extraction model must return.

These Pydantic models are handed to the API as the output schema, so the model
cannot return a shape the loader does not understand. Field descriptions are part
of the prompt — the model reads them — so they are written for the model, not
only for us.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal["mcq", "structured", "essay", "practical"]
McqOption = Literal["A", "B", "C", "D", "E"]


class BoundingBox(BaseModel):
    """Question bounds on the page, in PDF points, origin at top-left."""

    x0: float
    y0: float
    x1: float
    y1: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


class ExtractedQuestion(BaseModel):
    display_label: str = Field(
        description="Full question label exactly as printed, e.g. '1', '3(b)', '7(a)(ii)'."
    )
    question_type: QuestionType
    max_marks: int | None = Field(
        default=None,
        description="Marks in brackets at the end of the question, e.g. [3]. Null if absent.",
    )
    question_text: str = Field(
        description=(
            "The question's text, with LaTeX for mathematics ($v = u + at$). "
            "Describe any figure in square brackets, e.g. '[Figure: speed-time graph "
            "rising linearly from 0 to 20 m/s over 5 s]'. Do not attempt to transcribe "
            "the figure itself."
        )
    )
    bbox: BoundingBox = Field(
        description="Tight bounds around the whole question including its figures."
    )
    continues_on_next_page: bool = Field(
        default=False,
        description="True if this question is cut off by the page break.",
    )
    is_continuation: bool = Field(
        default=False,
        description="True if this question started on the previous page.",
    )
    options: dict[str, str] | None = Field(
        default=None,
        description="For MCQs only: {'A': 'text', 'B': ...}. Null for other types.",
    )


class ExtractedPage(BaseModel):
    page_number: int
    is_content_page: bool = Field(
        description=(
            "False for covers, blank pages, instruction pages, formula sheets and "
            "'BLANK PAGE' filler. Set this correctly — a cover parsed as questions "
            "poisons the bank."
        )
    )
    questions: list[ExtractedQuestion] = Field(default_factory=list)


class MarkSchemeEntry(BaseModel):
    display_label: str = Field(description="Question label this entry marks, e.g. '3(b)'.")
    marks: int | None = None
    content: str = Field(
        description=(
            "The marking points verbatim, preserving M/A/B mark notation and the "
            "examiner's own wording. Do not paraphrase — the wording is the product."
        )
    )


class MarkSchemeGrid(BaseModel):
    """MCQ mark schemes are an answer grid, not prose. Parsing them is trivial and
    exact, which is why the MCQ arena can ship long before structured papers are
    segmented reliably."""

    answers: dict[str, McqOption] = Field(
        description="Question number to correct option, e.g. {'1': 'C', '2': 'A'}."
    )


class TopicAssignment(BaseModel):
    topic_code: str = Field(
        description="A code from the supplied syllabus list. Never invent one."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(
        description="One sentence: which learning objective this question tests."
    )


class TopicTagging(BaseModel):
    primary: TopicAssignment
    secondary: list[TopicAssignment] = Field(
        default_factory=list,
        description=(
            "Additional topics genuinely tested. Leave empty rather than padding — "
            "a question tagged with five topics is useless for revision."
        ),
    )
