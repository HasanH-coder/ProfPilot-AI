"""Voice credentials and the 'Set up with AI' assistant."""

from fastapi import APIRouter, Depends

from app.ai.openai_service import OpenAIService, Usage
from app.api.deps import get_ai, get_db
from app.core.timing import timed
from app.db.supabase import Database
from app.schemas.api import RealtimeSecretIn, RealtimeSecretOut, SetupChatIn, SetupChatOut, SetupToolIn, SetupToolOut
from app.schemas.common import parse_id
from app.services.assessment_context import AssessmentContextService
from app.services.realtime_session import RealtimeSessionService
from app.services.setup_assistant import SETUP_TOOLS, SetupAssistantService, execute_setup_tool

router = APIRouter(prefix="/api", tags=["assistant"])

SETUP_TOOL_NAMES = {tool["name"] for tool in SETUP_TOOLS}


@router.post("/realtime/client-secrets")
async def create_client_secret(
    body: RealtimeSecretIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> RealtimeSecretOut:
    """A short-lived credential for one voice call. The permanent API key stays on the server."""
    result = await RealtimeSessionService(db, ai).create(
        body.purpose,
        setup=body.setup,
        assessment_id=parse_id(body.assessment_id, what="assessment") if body.assessment_id else None,
        exam_id=parse_id(body.exam_id, what="exam") if body.exam_id else None,
        addressed=body.addressed,
    )
    return RealtimeSecretOut.model_validate(result)


@router.post("/setup-assistant/tools/{tool_name}")
async def run_setup_tool(tool_name: str, body: SetupToolIn, db: Database = Depends(get_db)) -> SetupToolOut:
    """Applies one setup change the voice assistant chose, with the form's validation."""
    usage = Usage()
    async with timed(f"setup_tool.{tool_name if tool_name in SETUP_TOOL_NAMES else 'unknown'}", usage):
        courses = await AssessmentContextService(db).list_courses()
        setup, result = execute_setup_tool(body.setup, tool_name, body.arguments, courses, body.addressed)
    return SetupToolOut(setup=setup, result=result)


@router.post("/setup-assistant/messages")
async def setup_chat(body: SetupChatIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)) -> SetupChatOut:
    """The typed alternative to the voice call: same tools, same rules."""
    courses = await AssessmentContextService(db).list_courses()
    result = await SetupAssistantService(ai, courses).chat(
        body.setup, [message.model_dump() for message in body.messages], body.addressed
    )
    return SetupChatOut.model_validate(result)
