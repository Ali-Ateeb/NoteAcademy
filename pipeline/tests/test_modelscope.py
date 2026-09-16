"""The two pieces of modelscope.py worth testing without a network call:
stripping the markdown fence Qwen routinely wraps a JSON answer in, and
fixing the trailing comma it just as routinely leaves inside one — since
this endpoint's response_format asks for JSON but doesn't constrain decoding
to it the way Gemini's response_schema does.
"""

import json

from noteacademy_pipeline.modelscope import _fix_trailing_commas, _strip_json_fence


def test_strips_a_json_fence():
    text = '```json\n{"a": 1}\n```'
    assert _strip_json_fence(text) == '{"a": 1}'


def test_strips_a_bare_fence_with_no_language_tag():
    text = '```\n{"a": 1}\n```'
    assert _strip_json_fence(text) == '{"a": 1}'


def test_leaves_unfenced_json_alone():
    text = '{"a": 1}'
    assert _strip_json_fence(text) == '{"a": 1}'


def test_strips_surrounding_prose_when_unfenced():
    # No fence, but the object itself is still whitespace-trimmable.
    text = '  {"a": 1}  '
    assert _strip_json_fence(text) == '{"a": 1}'


def test_handles_a_fenced_array():
    text = '```json\n[{"a": 1}, {"b": 2}]\n```'
    assert _strip_json_fence(text) == '[{"a": 1}, {"b": 2}]'


def test_fixes_a_trailing_comma_before_a_closing_brace():
    # The exact shape a real reply produced: {"topic_code": "12.5", ..., }
    text = '{"topic_code": "12.5", "confidence": 0.9, "reasoning": "x", }'
    fixed = _fix_trailing_commas(text)
    assert json.loads(fixed) == {"topic_code": "12.5", "confidence": 0.9, "reasoning": "x"}


def test_fixes_a_trailing_comma_before_a_closing_bracket():
    text = '[{"a": 1}, {"b": 2}, ]'
    fixed = _fix_trailing_commas(text)
    assert json.loads(fixed) == [{"a": 1}, {"b": 2}]


def test_leaves_valid_json_unchanged():
    text = '{"a": 1, "b": [1, 2, 3]}'
    assert _fix_trailing_commas(text) == text
    json.loads(_fix_trailing_commas(text))  # still parses


def test_does_not_touch_a_comma_inside_a_string_value():
    # A comma followed by a quote-then-brace is not a trailing comma; only a
    # comma directly before the closer itself should ever be touched.
    text = '{"reasoning": "acid, base, and salt", "topic_code": "7.3"}'
    assert _fix_trailing_commas(text) == text
