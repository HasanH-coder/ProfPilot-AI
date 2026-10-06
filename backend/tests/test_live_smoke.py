"""Minimal LIVE checks against the real OpenAI API. Skipped unless explicitly requested.

They cost a little credit, so they never run in the normal test suite. Run them
deliberately, once, from backend/:

    PROFPILOT_LIVE_TESTS=1 .venv/bin/pytest tests/test_live_smoke.py -q

They use OPENAI_API_KEY from backend/.env and never print it.
"""

import os
import time

import pytest
from pydantic import BaseModel

from app.ai.openai_service import OpenAIService, Usage
from app.core.config import settings
from app.services.realtime_session import session_config
from app.services.setup_assistant import SETUP_TOOLS

pytestmark = pytest.mark.skipif(
    os.environ.get("PROFPILOT_LIVE_TESTS") != "1" or not settings.is_openai_configured,
    reason="live OpenAI checks run only with PROFPILOT_LIVE_TESTS=1 and an API key",
)


class SmokeAnswer(BaseModel):
    difficulty: str
    total_percent: int


@pytest.mark.anyio
async def test_reasoning_model_returns_structured_output():
    usage = Usage()
    answer = await OpenAIService().parse(
        purpose="live_smoke",
        instructions="Answer in the required JSON format.",
        input="An exam is 30% easy, 40% medium and 30% hard. Which difficulty has the largest share, and "
        "what do the three shares add up to?",
        schema=SmokeAnswer,
        effort="low",
        verbosity="low",
        max_output_tokens=2_000,
        usage=usage,
    )
    assert answer.difficulty.lower().startswith("medium") and answer.total_percent == 100
    assert usage.calls == 1


@pytest.mark.anyio
async def test_realtime_client_secret_is_short_lived():
    value, expires_at = await OpenAIService().create_realtime_client_secret(
        session=session_config(instructions="Smoke test session.", tools=SETUP_TOOLS),
        ttl_seconds=60,
        safety_identifier="profpilot-smoke-test",
    )
    assert value.startswith("ek_")
    assert time.time() < expires_at <= time.time() + 120
