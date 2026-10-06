"""Short-lived credentials for browser voice calls (OpenAI Realtime over WebRTC).

The permanent OPENAI_API_KEY never leaves the server. For each call, the API
mints an ephemeral client secret that expires quickly, with the session already
configured here (model, voice, instructions, tools). The browser uses it only
to open the call.

Tool calls the model makes during the call are executed by the browser through
this API's authenticated endpoints, so they get the same ownership checks and
validation as everything else.
"""

import hashlib
import time
from collections import defaultdict, deque
from typing import Any, Literal

from app.ai.openai_service import OpenAIService
from app.ai.prompts import builder_voice_instructions, setup_voice_instructions
from app.ai.tools import realtime_tools
from app.core.config import settings
from app.core.errors import AppError, ConflictError
from app.db.supabase import Database
from app.domain.setup import AssessmentSetup
from app.services.assessment_context import AssessmentContextService
from app.services.exam_builder import BUILDER_TOOLS, ExamBuilderService
from app.services.setup_assistant import SETUP_TOOLS, describe_courses, describe_setup

Purpose = Literal["setup", "builder"]

# At most this many voice credentials per professor in the window.
_RATE_LIMIT = 12
_RATE_WINDOW_SECONDS = 300
_recent: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(professor_id: str) -> None:
    now = time.monotonic()
    window = _recent[professor_id]
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _RATE_LIMIT:
        raise AppError("Too many voice calls were started. Please wait a few minutes.", code="rate_limited")
    window.append(now)


def session_config(*, instructions: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "realtime",
        "model": settings.realtime_model,
        "instructions": instructions,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "noise_reduction": {"type": "near_field"},
                "transcription": {"model": settings.realtime_transcription_model},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "auto",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": settings.realtime_voice},
        },
        "tools": realtime_tools(tools),
        "tool_choice": "auto",
        "reasoning": {"effort": "low"},
        "max_output_tokens": 2048,
        "truncation": "auto",
    }


class RealtimeSessionService:
    def __init__(self, db: Database, ai: OpenAIService) -> None:
        self.db = db
        self.ai = ai

    async def create(
        self,
        purpose: Purpose,
        *,
        setup: AssessmentSetup | None = None,
        assessment_id: str | None = None,
        exam_id: str | None = None,
    ) -> dict[str, Any]:
        _check_rate_limit(self.db.professor_id)
        context = AssessmentContextService(self.db)
        if purpose == "setup":
            if assessment_id:
                await context.get_assessment(assessment_id)  # must be the professor's own
            courses = await context.list_courses()
            instructions = setup_voice_instructions(
                describe_setup(setup or AssessmentSetup(), courses), describe_courses(courses)
            )
            tools = SETUP_TOOLS
        else:
            if not exam_id:
                raise ConflictError("Choose an exam to build.", code="exam_required")
            builder = ExamBuilderService(self.db, self.ai)
            exam = await builder.store.get_exam(exam_id)  # must be the professor's own
            if exam["mode"] != "interactive":
                raise ConflictError("This exam wasn't created with Build with AI.", code="not_interactive")
            instructions = builder_voice_instructions(await builder.state_summary(exam_id))
            tools = BUILDER_TOOLS

        value, expires_at = await self.ai.create_realtime_client_secret(
            session=session_config(instructions=instructions, tools=tools),
            ttl_seconds=min(max(settings.realtime_client_secret_seconds, 10), 7200),
            # A hashed id helps OpenAI detect abuse without sharing who the professor is.
            safety_identifier=hashlib.sha256(f"profpilot:{self.db.professor_id}".encode()).hexdigest(),
        )
        # Only what the browser needs: never the permanent key or the session internals.
        return {"clientSecret": value, "expiresAt": expires_at, "model": settings.realtime_model}
