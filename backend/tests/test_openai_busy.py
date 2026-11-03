"""A busy OpenAI service (a rate limit, an overload) is waited out instead of failing a long job; what
waiting can't fix (an exhausted balance, a request over the account's limit on its own) fails at once,
with a message that says what to do."""

import asyncio
from types import SimpleNamespace

import httpx2
import openai
import pytest
from pydantic import BaseModel

from app.ai import openai_service
from app.ai.openai_service import OpenAIService, _patiently, _translate_error
from app.core.errors import AIBusyError

URL = "https://api.openai.com/v1/responses"


def run(coroutine):
    return asyncio.run(coroutine)


def rate_limited(message="Rate limit reached for requests", *, code=None, headers=None):
    response = httpx2.Response(429, request=httpx2.Request("POST", URL), headers=headers or {})
    return openai.RateLimitError(message, response=response, body={"code": code, "message": message})


def overloaded():
    response = httpx2.Response(503, request=httpx2.Request("POST", URL))
    return openai.InternalServerError("The server is overloaded", response=response, body=None)


def rejected():
    response = httpx2.Response(400, request=httpx2.Request("POST", URL))
    return openai.BadRequestError("Invalid request", response=response, body={"code": "invalid_value"})


QUOTA = "You exceeded your current quota, please check your plan and billing details."
TOO_LARGE = "Request too large for gpt-6.1-sol in organization org-1 on tokens per min (TPM): Limit 30000, Requested 45678."


@pytest.fixture
def pauses(monkeypatch):
    waited: list[float] = []

    async def sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(openai_service.asyncio, "sleep", sleep)
    return waited


def replying(*replies):
    """A request that raises or returns each reply in turn."""
    queue = list(replies)

    async def call():
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return call


def test_a_busy_service_is_waited_out_as_long_as_openai_says(pauses):
    call = replying(rate_limited(headers={"retry-after-ms": "1500"}), overloaded(), "done")
    assert run(_patiently("generate_questions", call)) == "done"
    assert pauses == [1.5, 20.0]  # OpenAI's own wait, then the second pause


def test_a_service_still_busy_after_every_pause_is_reported_as_busy(pauses):
    call = replying(*[rate_limited() for _ in range(len(openai_service._BUSY_PAUSES) + 1)])
    with pytest.raises(openai.RateLimitError) as raised:
        run(_patiently("generate_questions", call))
    assert pauses == list(openai_service._BUSY_PAUSES)
    assert isinstance(_translate_error(raised.value, "generate_questions"), AIBusyError)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (rate_limited(QUOTA, code="insufficient_quota"), "ai_quota"),
        (rate_limited(TOO_LARGE, code="rate_limit_exceeded"), "ai_request_too_large"),
        (rejected(), "ai_bad_request"),
    ],
)
def test_what_waiting_cant_fix_fails_at_once_and_says_what_to_do(pauses, error, code):
    with pytest.raises(openai.OpenAIError):
        run(_patiently("plan_exam", replying(error, "never reached")))
    assert pauses == []
    translated = _translate_error(error, "plan_exam")
    assert translated.code == code
    if code == "ai_request_too_large":
        assert "usage tier" in translated.message


class Answer(BaseModel):
    text: str


def test_every_kind_of_request_waits_out_a_busy_service(pauses):
    reply = SimpleNamespace(status="completed", output=[], output_parsed=Answer(text="ok"), usage=None)
    vectors = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])], usage=SimpleNamespace(prompt_tokens=2))
    client = SimpleNamespace(
        responses=SimpleNamespace(parse=replying(rate_limited(), reply)),
        embeddings=SimpleNamespace(create=replying(overloaded(), vectors)),
    )
    client.responses.parse = _accepting(client.responses.parse)
    client.embeddings.create = _accepting(client.embeddings.create)
    service = OpenAIService(client)  # type: ignore[arg-type]
    answer = run(service.parse(purpose="plan_exam", instructions="", input="Plan it.", schema=Answer))
    assert answer.text == "ok"
    assert run(service.embed(["words"])) == [[0.1, 0.2]]
    assert pauses == [10.0, 10.0]


def _accepting(call):
    """The request above, called with the SDK's keyword arguments."""

    async def with_arguments(**_):
        return await call()

    return with_arguments
