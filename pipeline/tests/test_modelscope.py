"""The one piece of modelscope.py worth testing without a network call:
stripping the markdown fence Qwen routinely wraps a JSON answer in, since
this endpoint's response_format asks for JSON but doesn't constrain decoding
to it the way Gemini's response_schema does.
"""

from noteacademy_pipeline.modelscope import _strip_json_fence


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
