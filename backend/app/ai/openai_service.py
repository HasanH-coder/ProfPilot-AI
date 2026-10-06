"""The only place that talks to OpenAI.

Every AI feature goes through this service, so model choice, timeouts, retries,
error handling, structured-output validation and token accounting live in one
place. The API key stays here, on the server: it is never sent to the browser,
logged, or included in an error message.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import openai
import pydantic
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import AIBusyError, AIServiceError, AITimeoutError, AppError

logger = logging.getLogger("profpilot.ai")

T = TypeVar("T", bound=BaseModel)
# gpt-6.1-sol accepts low | medium | high | xhigh | max (not none/minimal).
Effort = Literal["low", "medium", "high", "xhigh", "max"]
Verbosity = Literal["low", "medium", "high"]

# Structured output is retried this many extra times when the reply is cut off,
# malformed, or breaks a business rule (with the problems fed back to the model).
_REPAIR_ATTEMPTS = 1
# The embeddings API accepts up to 2,048 inputs and 300,000 tokens per request.
_EMBED_BATCH_SIZE = 128
_EMBED_BATCH_CHARS = 400_000


@dataclass
class Usage:
    """Tokens and calls used by one job or request, recorded in ai_runs and timing logs (never the content)."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    # For timing logs (see app/core/timing.py): reasoning-model calls, embedding
    # requests, course-material searches, and the purpose of each model call.
    model_calls: int = 0
    embedding_calls: int = 0
    retrievals: int = 0
    purposes: list[str] = field(default_factory=list)

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.calls += 1

    def model_call(self, purpose: str) -> None:
        self.model_calls += 1
        self.purposes.append(purpose)


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolLoopResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


# A business-rule check: returns the problems found (empty when valid).
Validator = Callable[[T], list[str]]
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class OpenAIService:
    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            if not settings.is_openai_configured:
                raise AppError(
                    "ProfPilot's AI isn't set up on this server (OPENAI_API_KEY is missing).",
                    code="ai_not_configured",
                )
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),  # type: ignore[union-attr]
                timeout=settings.openai_timeout_seconds,
                max_retries=settings.openai_max_retries,
            )
        return self._client

    # -- Structured output -----------------------------------------------------

    async def parse(
        self,
        *,
        purpose: str,
        instructions: str,
        input: str | list[dict[str, Any]],
        schema: type[T],
        effort: Effort = "medium",
        verbosity: Verbosity = "medium",
        max_output_tokens: int = 32_000,
        validate: Validator[T] | None = None,
        usage: Usage | None = None,
    ) -> T:
        """Asks the reasoning model for an answer that matches `schema` exactly.

        If the answer is cut off, malformed, or breaks a business rule checked by
        `validate`, the model gets one more try with the problems spelled out.
        Malformed output is never returned.
        """
        messages: list[dict[str, Any]] = (
            [{"role": "user", "content": [{"type": "input_text", "text": input}]}] if isinstance(input, str) else list(input)
        )
        last_problems: list[str] = []
        for attempt in range(_REPAIR_ATTEMPTS + 1):
            attempt_input = messages
            if last_problems:
                attempt_input = [
                    *messages,
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Your previous answer could not be used because of these problems. "
                                "Return a complete, corrected answer that fixes all of them:\n- "
                                + "\n- ".join(last_problems[:20]),
                            }
                        ],
                    },
                ]
            started = time.monotonic()
            try:
                response = await self.client.responses.parse(
                    model=settings.reasoning_model,
                    instructions=instructions,
                    input=attempt_input,
                    text_format=schema,
                    text={"verbosity": verbosity},
                    reasoning={"effort": effort},
                    max_output_tokens=max_output_tokens,
                    store=False,
                )
            except pydantic.ValidationError:
                # The reply was cut off or didn't match the schema.
                logger.warning("%s: unparseable reply (attempt %s)", purpose, attempt + 1)
                last_problems = ["The reply was incomplete or did not match the required JSON structure."]
                max_output_tokens = min(int(max_output_tokens * 1.5), 128_000)
                continue
            except openai.OpenAIError as error:
                raise _translate_error(error, purpose) from error

            self._record_usage(purpose, response, usage, started)
            refusal = _refusal_text(response)
            if refusal:
                raise AIServiceError(
                    "The AI declined to complete this request. Try rephrasing your instructions.",
                    code="ai_refused",
                )
            if getattr(response, "status", None) == "incomplete":
                reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
                if reason == "content_filter":
                    raise AIServiceError(
                        "The AI couldn't complete this because of its content policy.",
                        code="ai_content_filter",
                    )
                logger.warning("%s: reply cut off (%s), attempt %s", purpose, reason, attempt + 1)
                last_problems = ["The reply was cut off before it was complete. Be more concise where possible."]
                max_output_tokens = min(int(max_output_tokens * 1.5), 128_000)
                continue

            parsed = response.output_parsed
            if parsed is None:
                last_problems = ["The reply did not contain the required JSON object."]
                continue
            problems = validate(parsed) if validate else []
            if not problems:
                return parsed
            logger.info("%s: %d rule problem(s), attempt %s", purpose, len(problems), attempt + 1)
            last_problems = problems

        raise AIServiceError(
            "The AI's answer didn't pass ProfPilot's quality checks. Please try again.",
            code="ai_invalid_output",
        )

    # -- Tool calling (text conversations) -------------------------------------

    async def run_tool_loop(
        self,
        *,
        purpose: str,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        execute: ToolExecutor,
        effort: Effort = "low",
        max_rounds: int = 6,
        usage: Usage | None = None,
    ) -> ToolLoopResult:
        """Lets the model call application tools until it has a reply for the professor.

        Tools run through `execute`, which enforces the same validation and
        ownership checks as the REST endpoints. Nothing is stored at OpenAI
        between calls (store=False); the conversation is replayed instead.
        """
        items: list[Any] = list(messages)
        calls_made: list[ToolCall] = []
        for _round in range(max_rounds):
            started = time.monotonic()
            try:
                response = await self.client.responses.create(
                    model=settings.reasoning_model,
                    instructions=instructions,
                    input=items,
                    tools=list(tools),
                    tool_choice="auto",
                    reasoning={"effort": effort},
                    text={"verbosity": "low"},
                    max_output_tokens=16_000,
                    store=False,
                    include=["reasoning.encrypted_content"],
                )
            except openai.OpenAIError as error:
                raise _translate_error(error, purpose) from error
            self._record_usage(purpose, response, usage, started)

            function_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
            if not function_calls:
                return ToolLoopResult(text=(response.output_text or "").strip(), tool_calls=calls_made)

            items.extend(item.model_dump(exclude_none=True) for item in response.output)
            for call in function_calls:
                try:
                    arguments = json.loads(call.arguments or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError
                except ValueError:
                    result: dict[str, Any] = {"ok": False, "error": "The arguments were not valid JSON."}
                else:
                    calls_made.append(ToolCall(call_id=call.call_id, name=call.name, arguments=arguments))
                    try:
                        result = await execute(call.name, arguments)
                    except AppError as error:
                        result = {"ok": False, "error": error.message}
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
        return ToolLoopResult(
            text="I've made the changes I could. Tell me what you'd like to do next.",
            tool_calls=calls_made,
        )

    # -- Images and PDFs ---------------------------------------------------------

    async def parse_with_files(
        self,
        *,
        purpose: str,
        instructions: str,
        text: str,
        files: Sequence[dict[str, Any]],
        schema: type[T],
        effort: Effort = "low",
        max_output_tokens: int = 32_000,
        usage: Usage | None = None,
    ) -> T:
        """Structured output from images or PDF pages (e.g. a scanned exam)."""
        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}, *files]
        return await self.parse(
            purpose=purpose,
            instructions=instructions,
            input=[{"role": "user", "content": content}],
            schema=schema,
            effort=effort,
            verbosity="medium",
            max_output_tokens=max_output_tokens,
            usage=usage,
        )

    # -- Embeddings ------------------------------------------------------------

    async def embed(self, texts: Sequence[str], *, usage: Usage | None = None) -> list[list[float]]:
        """One vector per text, in order."""
        vectors: list[list[float]] = []
        for batch in _batches(texts):
            try:
                result = await self.client.embeddings.create(
                    model=settings.embedding_model,
                    input=batch,
                    dimensions=settings.embedding_dimensions,
                )
            except openai.OpenAIError as error:
                raise _translate_error(error, "embeddings") from error
            ordered = sorted(result.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
            if usage is not None:
                usage.embedding_calls += 1
                if result.usage is not None:
                    usage.add(result.usage.prompt_tokens, 0)
        return vectors

    # -- Realtime (voice) --------------------------------------------------------

    async def create_realtime_client_secret(
        self, *, session: dict[str, Any], ttl_seconds: int, safety_identifier: str
    ) -> tuple[str, int]:
        """A short-lived key the browser uses to open one voice call over WebRTC."""
        try:
            secret = await self.client.realtime.client_secrets.create(
                expires_after={"anchor": "created_at", "seconds": ttl_seconds},
                session=session,
                extra_headers={"OpenAI-Safety-Identifier": safety_identifier},
            )
        except openai.OpenAIError as error:
            raise _translate_error(error, "realtime_client_secret") from error
        return secret.value, int(secret.expires_at)

    # -- Internals -----------------------------------------------------------------

    @staticmethod
    def _record_usage(purpose: str, response: Any, usage: Usage | None, started: float) -> None:
        response_usage = getattr(response, "usage", None)
        input_tokens = getattr(response_usage, "input_tokens", 0) or 0
        output_tokens = getattr(response_usage, "output_tokens", 0) or 0
        if usage is not None:
            usage.add(input_tokens, output_tokens)
            usage.model_call(purpose)
        # Purpose, size and time only: never prompts, files or answers.
        logger.info(
            "%s: %s in / %s out tokens, %.1fs",
            purpose,
            input_tokens,
            output_tokens,
            time.monotonic() - started,
        )


def _refusal_text(response: Any) -> str | None:
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return getattr(part, "refusal", None) or "refused"
    return None


def _translate_error(error: Exception, purpose: str) -> AppError:
    """OpenAI errors become safe messages. Details are logged without content."""
    if isinstance(error, openai.RateLimitError):
        logger.warning("%s: rate limited", purpose)
        return AIBusyError()
    if isinstance(error, openai.APITimeoutError):
        logger.warning("%s: timed out", purpose)
        return AITimeoutError()
    if isinstance(error, openai.APIConnectionError):
        logger.warning("%s: connection error", purpose)
        return AIServiceError("ProfPilot couldn't reach its AI service. Please try again.")
    if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        logger.error("%s: OpenAI rejected the server's credentials", purpose)
        return AIServiceError("ProfPilot's AI service isn't available right now.", code="ai_not_configured")
    if isinstance(error, openai.BadRequestError):
        logger.error("%s: bad request (%s)", purpose, getattr(error, "code", None))
        return AIServiceError("The AI couldn't process this request.", code="ai_bad_request")
    if isinstance(error, openai.APIStatusError):
        logger.warning("%s: OpenAI status %s", purpose, error.status_code)
        if error.status_code >= 500:
            return AIBusyError("ProfPilot's AI service had a temporary problem. Please try again.")
        return AIServiceError()
    logger.warning("%s: %s", purpose, type(error).__name__)
    return AIServiceError()


def _batches(texts: Sequence[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and (len(current) >= _EMBED_BATCH_SIZE or size + len(text) > _EMBED_BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        batches.append(current)
    return batches


async def gather_limited(limit: int, coroutines: Sequence[Awaitable[Any]]) -> list[Any]:
    """Runs coroutines concurrently, at most `limit` at a time, keeping order.

    If one fails, the others are cancelled (so nothing keeps writing after a
    job has failed) and the original error is raised.
    """
    semaphore = asyncio.Semaphore(max(1, limit))
    results: list[Any] = [None] * len(coroutines)

    async def run(index: int, coroutine: Awaitable[Any]) -> None:
        started = False
        try:
            async with semaphore:
                started = True
                results[index] = await coroutine
        finally:
            if not started and hasattr(coroutine, "close"):
                coroutine.close()  # cancelled before it began

    try:
        async with asyncio.TaskGroup() as group:
            for index, coroutine in enumerate(coroutines):
                group.create_task(run(index, coroutine))
    except BaseExceptionGroup as errors:
        raise _first_error(errors) from None
    return results


def _first_error(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


_service: OpenAIService | None = None


def get_openai_service() -> OpenAIService:
    global _service
    if _service is None:
        _service = OpenAIService()
    return _service
