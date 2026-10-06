"""The OpenAI wrapper: retries with feedback, refusals, cut-off replies, and safe errors.

A fake SDK client stands in for OpenAI, so these tests make no network calls.
"""

from types import SimpleNamespace

import httpx2
import openai
import pydantic
import pytest
from pydantic import BaseModel

from app.ai.openai_service import OpenAIService, Usage
from app.core.errors import AIBusyError, AIServiceError, AITimeoutError


class Answer(BaseModel):
    total: int


def response(parsed=None, *, status="completed", reason=None, refusal=None, output=None):
    content = [SimpleNamespace(type="refusal", refusal=refusal)] if refusal else []
    return SimpleNamespace(
        output_parsed=parsed,
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        output=output if output is not None else [SimpleNamespace(type="message", content=content)],
        output_text="",
        usage=SimpleNamespace(input_tokens=100, output_tokens=40),
    )


class FakeResponses:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def parse(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    create = parse


def service(*replies):
    responses = FakeResponses(replies)
    client = SimpleNamespace(responses=responses)
    return OpenAIService(client=client), responses


@pytest.mark.anyio
async def test_structured_output_uses_the_reasoning_model_without_storing():
    ai, responses = service(response(Answer(total=100)))
    usage = Usage()
    result = await ai.parse(purpose="t", instructions="i", input="x", schema=Answer, effort="high", usage=usage)
    assert result.total == 100
    request = responses.requests[0]
    assert request["model"] == "gpt-6.1-sol"
    assert request["reasoning"] == {"effort": "high"}
    assert request["text_format"] is Answer and request["store"] is False
    assert "temperature" not in request
    assert (usage.input_tokens, usage.output_tokens) == (100, 40)


@pytest.mark.anyio
async def test_business_rule_problems_are_fed_back_once():
    ai, responses = service(response(Answer(total=90)), response(Answer(total=100)))
    result = await ai.parse(
        purpose="t",
        instructions="i",
        input="x",
        schema=Answer,
        validate=lambda answer: [] if answer.total == 100 else ["The total must be 100."],
    )
    assert result.total == 100
    feedback = responses.requests[1]["input"][-1]["content"][0]["text"]
    assert "The total must be 100." in feedback


@pytest.mark.anyio
async def test_invalid_output_is_never_returned():
    ai, _ = service(response(Answer(total=1)), response(Answer(total=2)))
    with pytest.raises(AIServiceError) as error:
        await ai.parse(purpose="t", instructions="i", input="x", schema=Answer, validate=lambda a: ["still wrong"])
    assert error.value.code == "ai_invalid_output"


@pytest.mark.anyio
async def test_cut_off_or_malformed_replies_are_retried_with_more_room():
    malformed = pydantic.ValidationError.from_exception_data("Answer", [])
    ai, responses = service(malformed, response(Answer(total=3)))
    assert (await ai.parse(purpose="t", instructions="i", input="x", schema=Answer, max_output_tokens=1000)).total == 3
    assert responses.requests[1]["max_output_tokens"] == 1500
    ai, _ = service(response(None, status="incomplete", reason="max_output_tokens"), response(Answer(total=4)))
    assert (await ai.parse(purpose="t", instructions="i", input="x", schema=Answer)).total == 4


@pytest.mark.anyio
async def test_refusals_and_content_filters_become_clear_errors():
    ai, _ = service(response(None, refusal="I can't help with that."))
    with pytest.raises(AIServiceError) as error:
        await ai.parse(purpose="t", instructions="i", input="x", schema=Answer)
    assert error.value.code == "ai_refused"
    ai, _ = service(response(None, status="incomplete", reason="content_filter"))
    with pytest.raises(AIServiceError) as error:
        await ai.parse(purpose="t", instructions="i", input="x", schema=Answer)
    assert error.value.code == "ai_content_filter"


@pytest.mark.anyio
async def test_sdk_errors_are_translated_without_details():
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    rate_limited = openai.RateLimitError(
        "secret detail", response=httpx2.Response(429, request=request), body={"message": "secret detail"}
    )
    ai, _ = service(rate_limited)
    with pytest.raises(AIBusyError) as error:
        await ai.parse(purpose="t", instructions="i", input="x", schema=Answer)
    assert "secret" not in error.value.message
    ai, _ = service(openai.APITimeoutError(request=request))
    with pytest.raises(AITimeoutError):
        await ai.parse(purpose="t", instructions="i", input="x", schema=Answer)


@pytest.mark.anyio
async def test_tool_loop_runs_tools_and_returns_the_reply():
    call = SimpleNamespace(
        type="function_call",
        call_id="c1",
        name="get_exam_state",
        arguments="{}",
        model_dump=lambda **_: {"type": "function_call", "call_id": "c1", "name": "get_exam_state", "arguments": "{}"},
    )
    first = response(output=[call])
    second = response(output=[])
    second.output_text = "Here is the exam."
    ai, responses = service(first, second)
    executed = []

    async def execute(name, arguments):
        executed.append((name, arguments))
        return {"ok": True}

    result = await ai.run_tool_loop(
        purpose="t", instructions="i", messages=[{"role": "user", "content": "hi"}], tools=[], execute=execute
    )
    assert executed == [("get_exam_state", {})]
    assert result.text == "Here is the exam."
    replay = responses.requests[1]["input"]
    assert replay[-1]["type"] == "function_call_output" and replay[-1]["call_id"] == "c1"
    assert responses.requests[1]["store"] is False


def test_the_api_key_is_never_part_of_settings_repr(monkeypatch):
    from app.core.config import Settings

    settings = Settings(openai_api_key="sk-test-should-not-leak")
    assert "sk-test-should-not-leak" not in repr(settings)
    assert "sk-test-should-not-leak" not in str(settings.model_dump())
