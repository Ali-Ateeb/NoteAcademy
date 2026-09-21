"""deepseek.py without a network call.

Two kinds of test. The reply-repair helpers (fence, trailing comma) are pure
functions. The client itself is exercised through `httpx.MockTransport`, so
what is asserted is the actual request a real server would receive — the
contract the module docstring lists — and how each documented failure is
handled. What none of this can prove is that DeepSeek's live API accepts that
request; that needs a real key, and is called out wherever it matters.
"""

import dataclasses
import json
from types import SimpleNamespace

import httpx
import pytest

from noteacademy_pipeline import deepseek
from noteacademy_pipeline.config import Settings
from noteacademy_pipeline.deepseek import (
    DeepSeekAccountError,
    DeepSeekError,
    _fix_trailing_commas,
    _strip_json_fence,
    chat_json,
    image_content_block,
    text_content_block,
)

# --------------------------------------------------------------------------
# reply repair (pure)
# --------------------------------------------------------------------------


def test_strips_a_json_fence():
    assert _strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strips_a_bare_fence_with_no_language_tag():
    assert _strip_json_fence('```\n{"a": 1}\n```') == '{"a": 1}'


def test_leaves_unfenced_json_alone():
    assert _strip_json_fence('{"a": 1}') == '{"a": 1}'


def test_trims_whitespace_around_unfenced_json():
    assert _strip_json_fence('  {"a": 1}  ') == '{"a": 1}'


def test_handles_a_fenced_array():
    assert _strip_json_fence('```json\n[{"a": 1}, {"b": 2}]\n```') == '[{"a": 1}, {"b": 2}]'


def test_fixes_a_trailing_comma_before_a_closing_brace():
    text = '{"topic_code": "12.5", "confidence": 0.9, "reasoning": "x", }'
    assert json.loads(_fix_trailing_commas(text)) == {
        "topic_code": "12.5", "confidence": 0.9, "reasoning": "x",
    }


def test_fixes_a_trailing_comma_before_a_closing_bracket():
    assert json.loads(_fix_trailing_commas('[{"a": 1}, {"b": 2}, ]')) == [{"a": 1}, {"b": 2}]


def test_leaves_valid_json_unchanged():
    text = '{"a": 1, "b": [1, 2, 3]}'
    assert _fix_trailing_commas(text) == text


def test_does_not_touch_a_comma_inside_a_string_value():
    text = '{"reasoning": "acid, base, and salt", "topic_code": "7.3"}'
    assert _fix_trailing_commas(text) == text


# --------------------------------------------------------------------------
# content blocks
# --------------------------------------------------------------------------


def test_image_block_is_an_openai_style_data_url():
    block = image_content_block(b"\x89PNG-bytes")
    assert block["type"] == "image_url"
    assert block["image_url"]["url"].startswith("data:image/png;base64,")
    # Not Anthropic's shape, which the replaced client used.
    assert "source" not in block


def test_text_block_shape():
    assert text_content_block("hi") == {"type": "text", "text": "hi"}


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------


def completion(content: str | None, finish_reason: str = "stop") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish_reason}]}


class Server:
    """A scripted DeepSeek: each request gets the next response in the list
    (the last one repeats), and every request is kept for inspection."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[index]

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].content)


def ok(content: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, json=completion(content, **kwargs))


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """A key present, thinking off, and no real sleeping between retries."""
    monkeypatch.setattr(
        deepseek, "settings",
        dataclasses.replace(Settings(), deepseek_api_key="test-key", deepseek_thinking=False),
    )
    monkeypatch.setattr(deepseek.time, "sleep", lambda seconds: None)


def call(server: Server, **overrides) -> str:
    kwargs = dict(model="deepseek-flash", system="Classify. Reply as JSON.",
                  user_content="a question", client=server.client)
    kwargs.update(overrides)
    return chat_json(**kwargs)


def test_sends_the_documented_request():
    server = Server(ok('{"a": 1}'))
    assert call(server) == '{"a": 1}'

    request = server.requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert "x-api-key" not in request.headers  # the replaced client's header

    body = server.body
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    # The system prompt is the first message, not a top-level field.
    assert body["messages"] == [
        {"role": "system", "content": "Classify. Reply as JSON."},
        {"role": "user", "content": "a question"},
    ]
    assert "system" not in body


def test_thinking_is_stated_off_because_the_apis_own_default_is_on():
    server = Server(ok("{}"))
    call(server)
    assert server.body["thinking"] == {"type": "disabled"}
    assert server.body["temperature"] == 0


def test_thinking_on_widens_the_budget_and_drops_temperature(monkeypatch):
    monkeypatch.setattr(
        deepseek, "settings",
        dataclasses.replace(Settings(), deepseek_api_key="k", deepseek_thinking=True),
    )
    server = Server(ok("{}"))
    call(server, max_tokens=1024)

    assert server.body["thinking"] == {"type": "enabled"}
    # A 1,024-token budget would be spent reasoning, leaving no answer.
    assert server.body["max_tokens"] >= 16000
    assert "temperature" not in server.body  # ignored in thinking mode


def test_a_larger_budget_is_never_shrunk_by_thinking(monkeypatch):
    monkeypatch.setattr(
        deepseek, "settings",
        dataclasses.replace(Settings(), deepseek_api_key="k", deepseek_thinking=True),
    )
    server = Server(ok("{}"))
    call(server, max_tokens=50000)
    assert server.body["max_tokens"] == 50000


def test_the_word_json_is_added_when_a_prompt_forgets_it():
    server = Server(ok("{}"))
    call(server, system="Classify the question.", user_content="a question")
    assert "json" in server.body["messages"][0]["content"].lower()


def test_the_prompt_is_left_alone_when_it_already_says_json():
    server = Server(ok("{}"))
    call(server, system="Classify.", user_content="Respond with ONLY a JSON object")
    assert server.body["messages"][0]["content"] == "Classify."


def test_json_mentioned_only_in_a_text_block_counts():
    server = Server(ok("{}"))
    call(server, system="Classify.", user_content=[text_content_block("answer in JSON")])
    assert server.body["messages"][0]["content"] == "Classify."


def test_a_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.setattr(deepseek, "settings", dataclasses.replace(Settings(), deepseek_api_key=""))
    server = Server(ok("{}"))
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        call(server)
    assert server.requests == []


def test_a_fenced_reply_with_a_trailing_comma_comes_back_valid():
    server = Server(ok('```json\n{"a": 1, }\n```'))
    assert json.loads(call(server)) == {"a": 1}


def test_the_keepalive_blank_lines_before_a_reply_are_harmless():
    # While a request queues, the server sends empty lines ahead of the body.
    body = "\n\n\n" + json.dumps(completion('{"a": 1}'))
    server = Server(httpx.Response(200, content=body.encode()))
    assert call(server) == '{"a": 1}'


# ---- images ---------------------------------------------------------------


def test_an_image_goes_in_the_user_message_only():
    server = Server(ok("{}"))
    call(server, user_content=[text_content_block("json please"), image_content_block(b"png")])

    system, user = server.body["messages"]
    assert system["role"] == "system" and isinstance(system["content"], str)
    assert [b["type"] for b in user["content"]] == ["text", "image_url"]


def test_the_text_only_model_refuses_an_image_before_the_network():
    server = Server(ok("{}"))
    with pytest.raises(ValueError, match="does not accept images"):
        call(server, model="deepseek-v4-pro", user_content=[image_content_block(b"png")])
    assert server.requests == []


def test_the_text_only_model_is_fine_with_text():
    server = Server(ok('{"a": 1}'))
    assert call(server, model="deepseek-v4-pro") == '{"a": 1}'


# ---- failures that must not be retried ---------------------------------------


@pytest.mark.parametrize("status,phrase", [(401, "API key"), (402, "balance")])
def test_a_bad_key_or_empty_balance_stops_at_once_as_an_account_error(status, phrase):
    server = Server(httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(DeepSeekAccountError, match=phrase):
        call(server)
    assert len(server.requests) == 1


def test_an_account_error_is_a_deepseek_error():
    assert issubclass(DeepSeekAccountError, DeepSeekError)


@pytest.mark.parametrize("status", [400, 422])
def test_a_rejected_request_surfaces_the_servers_reason_and_is_not_retried(status):
    server = Server(httpx.Response(status, text="Images in system messages are not supported"))
    with pytest.raises(DeepSeekError, match="Images in system messages"):
        call(server)
    assert len(server.requests) == 1


def test_a_reply_cut_off_by_the_token_budget_is_not_retried_and_says_why():
    server = Server(ok("", finish_reason="length"))
    with pytest.raises(DeepSeekError, match="THINKING"):
        call(server)
    assert len(server.requests) == 1


# ---- failures that are retried -------------------------------------------------


def test_a_429_is_retried_and_then_succeeds():
    server = Server(httpx.Response(429), httpx.Response(429), ok('{"a": 1}'))
    assert call(server) == '{"a": 1}'
    assert len(server.requests) == 3


def test_retry_after_is_honoured(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(deepseek.time, "sleep", sleeps.append)
    server = Server(httpx.Response(429, headers={"Retry-After": "45"}), ok("{}"))
    call(server)
    assert sleeps == [45.0]  # longer than the 3s backoff it would otherwise use


def test_backoff_is_exponential_and_capped(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(deepseek.time, "sleep", sleeps.append)
    server = Server(*[httpx.Response(503)] * 5, ok("{}"))
    call(server)
    assert sleeps == [3.0, 6.0, 12.0, 24.0, 48.0]


@pytest.mark.parametrize("status", [500, 503])
def test_a_persistent_server_error_gives_up_after_the_attempt_limit(status):
    server = Server(httpx.Response(status))
    with pytest.raises(httpx.HTTPStatusError):
        call(server)
    assert len(server.requests) == deepseek._MAX_ATTEMPTS


def test_a_dropped_connection_is_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("server disconnected without sending a response")
        return ok('{"a": 1}')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert chat_json(model="deepseek-flash", system="Reply as JSON.", user_content="q",
                     client=client) == '{"a": 1}'
    assert calls["n"] == 2


def test_an_empty_reply_is_retried_because_the_docs_say_it_happens():
    server = Server(ok(""), ok(None), ok('{"a": 1}'))
    assert call(server) == '{"a": 1}'
    assert len(server.requests) == 3


def test_a_reply_that_stays_empty_eventually_fails_as_a_deepseek_error():
    server = Server(ok(""))
    with pytest.raises(DeepSeekError, match="no usable reply"):
        call(server)
    assert len(server.requests) == deepseek._MAX_ATTEMPTS


def test_a_200_that_is_not_a_chat_completion_is_retried():
    server = Server(httpx.Response(200, text="<html>bad gateway</html>"), ok('{"a": 1}'))
    assert call(server) == '{"a": 1}'
    assert len(server.requests) == 2


# --------------------------------------------------------------------------
# how the pipeline uses it
# --------------------------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    """Replaces chat_json with a recorder so a call site's own choices — which
    model, what order — can be asserted without an HTTP layer."""
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return '{"topic_code": "1.1", "confidence": 0.9, "reasoning": "x"}'

    monkeypatch.setattr(deepseek, "chat_json", fake)
    return calls


def _topics():
    from noteacademy_pipeline.tagging import TopicOption

    return [TopicOption(code="1.1", title="Physical quantities", learning_objectives=["Measure"])]


def test_tag_from_crop_uses_the_vision_model_and_puts_the_syllabus_before_the_image(
    captured, tmp_path, monkeypatch
):
    from noteacademy_pipeline import tagging

    monkeypatch.setattr(
        tagging, "settings",
        dataclasses.replace(Settings(), deepseek_vision_model="deepseek-flash"),
    )
    crop = tmp_path / "q.png"
    crop.write_bytes(b"png")

    assignment = tagging.tag_from_crop(crop, _topics())

    assert assignment.topic_code == "1.1"
    call_ = captured[0]
    assert call_["model"] == "deepseek-flash"
    kinds = [b["type"] for b in call_["user_content"]]
    # Prefix caching: the constant syllabus must come before the varying crop.
    assert kinds == ["text", "image_url", "text"]
    assert "Syllabus topics" in call_["user_content"][0]["text"]
    assert "JSON" in call_["user_content"][2]["text"]


def test_text_tagging_uses_the_text_model_and_leads_with_the_syllabus(captured, monkeypatch):
    from noteacademy_pipeline import tagging

    monkeypatch.setattr(
        tagging, "settings",
        dataclasses.replace(
            Settings(), tagging_provider="deepseek", deepseek_text_model="deepseek-v4-pro"
        ),
    )
    reply = json.dumps({"primary": {"topic_code": "1.1", "confidence": 0.9, "reasoning": "x"},
                        "secondary": []})
    monkeypatch.setattr(deepseek, "chat_json", lambda **kw: captured.append(kw) or reply)

    result = tagging.tag_question("What is a metre?", _topics(), mark_scheme="A unit of length")

    assert result.primary.topic_code == "1.1"
    call_ = captured[0]
    assert call_["model"] == "deepseek-v4-pro"
    assert call_["user_content"].index("Syllabus topics") < call_["user_content"].index("Question:")
    assert isinstance(call_["user_content"], str)  # text only: no image, so the pro model is fine


def test_extraction_sends_the_page_image_to_the_vision_model(captured, tmp_path, monkeypatch):
    from noteacademy_pipeline import extract

    monkeypatch.setattr(
        extract, "settings",
        dataclasses.replace(Settings(), deepseek_vision_model="deepseek-flash"),
    )
    png = tmp_path / "p.png"
    png.write_bytes(b"png")
    page = SimpleNamespace(page_number=3, png_path=png, text="", width_pt=595.0, height_pt=842.0,
                           has_text_layer=False)
    reply = json.dumps({"page_number": 3, "is_content_page": False, "questions": []})
    monkeypatch.setattr(deepseek, "chat_json", lambda **kw: captured.append(kw) or reply)

    result = extract._extract_page_deepseek(page)

    assert result.is_content_page is False
    call_ = captured[0]
    assert call_["model"] == "deepseek-flash"
    assert any(b["type"] == "image_url" for b in call_["user_content"])
    assert call_["max_tokens"] == 16000


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_defaults_pair_the_stronger_text_model_with_the_only_vision_model():
    settings = Settings()
    assert settings.deepseek_text_model == "deepseek-v4-pro"
    assert settings.deepseek_vision_model == "deepseek-flash"
    assert settings.deepseek_vision_model not in deepseek._TEXT_ONLY_MODELS
    assert settings.deepseek_thinking is False


def test_gemini_stays_the_default_provider():
    assert Settings().tagging_provider == "gemini"
    assert Settings().extraction_provider == "gemini"


@pytest.mark.parametrize("var", ["NOTEACADEMY_TAGGING_PROVIDER", "NOTEACADEMY_EXTRACTION_PROVIDER"])
def test_a_removed_or_mistyped_provider_fails_loudly_instead_of_running_gemini(monkeypatch, var):
    monkeypatch.setenv(var, "modelscope")
    with pytest.raises(ValueError, match="not a known provider"):
        Settings.from_env()


def test_deepseek_is_selectable_per_stage(monkeypatch):
    monkeypatch.setenv("NOTEACADEMY_TAGGING_PROVIDER", " DeepSeek ")
    monkeypatch.setenv("NOTEACADEMY_EXTRACTION_PROVIDER", "gemini")
    settings = Settings.from_env()
    assert (settings.tagging_provider, settings.extraction_provider) == ("deepseek", "gemini")


@pytest.mark.parametrize("value,expected", [("1", True), ("on", True), ("TRUE", True),
                                            ("", False), ("0", False), ("off", False)])
def test_thinking_switch_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("NOTEACADEMY_DEEPSEEK_THINKING", value)
    assert Settings.from_env().deepseek_thinking is expected
