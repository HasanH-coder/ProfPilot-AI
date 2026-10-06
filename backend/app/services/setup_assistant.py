"""'Set up with AI': tools that fill in the Assessment Setup form by conversation.

The executor is pure: it takes the form's current setup and a tool call, and
returns the new setup (or a clear error) using exactly the form's validation
rules. The browser shows the change immediately and saves it to the existing
draft. Voice (Realtime) and text chat use the same tools and the same executor.
"""

from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import setup_chat_instructions
from app.ai.tools import function_tool
from app.core.errors import InvalidInputError
from app.domain.setup import LIMITS, AssessmentSetup, check_difficulty_distribution
from app.services.assessment_context import CourseInfo

_PERCENT = {"type": ["integer", "null"], "minimum": 0, "maximum": 100}
_TEXT_MODE = {"type": "string", "enum": ["append", "replace", "clear"]}

SETUP_TOOLS: list[dict[str, Any]] = [
    function_tool("get_current_setup", "Read the assessment setup as it is on the professor's screen now.", {}),
    function_tool(
        "set_assessment_name",
        "Set the assessment's name (e.g. 'Midterm'), or null to clear it.",
        {"name": {"type": ["string", "null"]}},
    ),
    function_tool(
        "set_course",
        "Choose one of the professor's courses by id, or null for no course.",
        {"course_id": {"type": ["string", "null"]}},
    ),
    function_tool(
        "set_duration",
        "Set the duration in minutes, or null when the professor doesn't want to specify it.",
        {"minutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 1440}},
    ),
    function_tool(
        "set_question_distribution",
        "Set the share of marks in multiple-choice questions (the rest is subjective), or null to clear it.",
        {"mcq_percent": _PERCENT},
    ),
    function_tool(
        "set_difficulty_distribution",
        "Set the share of marks that is easy, medium and hard (must add up to 100), or all null to clear it.",
        {"easy_percent": _PERCENT, "medium_percent": _PERCENT, "hard_percent": _PERCENT},
    ),
    function_tool(
        "set_versions",
        "Set how many equivalent versions of the exam to create (1–20).",
        {"count": {"type": "integer", "minimum": 1, "maximum": 20}},
    ),
    function_tool(
        "update_notes",
        "Change the 'Additional notes' (coverage, focus, constraints). Prefer append.",
        {"text": {"type": "string"}, "mode": _TEXT_MODE},
    ),
    function_tool(
        "update_professor_prompt",
        "Change the 'Tell ProfPilot what you want' instructions. Prefer append.",
        {"text": {"type": "string"}, "mode": _TEXT_MODE},
    ),
    function_tool(
        "finish_setup",
        "Call when the professor is done setting up.",
        {"summary": {"type": ["string", "null"], "description": "A one-sentence summary of the setup."}},
    ),
]


def describe_setup(setup: AssessmentSetup, courses: list[CourseInfo]) -> str:
    course = next((c for c in courses if c.id == setup.course_id), None)
    missing = "not set"
    return "\n".join(
        [
            f"- Course: {course.label + ' (id ' + course.id + ')' if course else missing}",
            f"- Assessment name: {setup.exam_name.strip() or missing}",
            f"- Duration: {setup.duration_text or missing}",
            f"- Question format: {setup.format_text or missing}",
            f"- Difficulty: {setup.difficulty_text or missing}",
            f"- Versions: {setup.number_of_versions}",
            f"- Notes: {setup.additional_notes.strip() or missing}",
            f"- Instructions: {setup.professor_prompt.strip() or missing}",
        ]
    )


def describe_courses(courses: list[CourseInfo]) -> str:
    if not courses:
        return "The professor has no courses yet (they can add one on the page)."
    return "\n".join(f"- {course.label}: id {course.id}" for course in courses)


def execute_setup_tool(
    setup: AssessmentSetup, name: str, arguments: dict[str, Any], courses: list[CourseInfo]
) -> tuple[AssessmentSetup, dict[str, Any]]:
    """Applies one tool call. Returns the new setup and a result for the model."""
    current = setup.model_copy()
    changes: dict[str, Any] = {}
    message = ""

    def text_update(field: str, label: str) -> None:
        nonlocal message
        mode = arguments.get("mode")
        text = (arguments.get("text") or "").strip()
        old = getattr(current, field)
        if mode == "clear":
            new = ""
        elif mode == "replace":
            new = text
        elif mode == "append":
            if not text:
                raise InvalidInputError("There's nothing to add.")
            new = f"{old.rstrip()}\n{text}" if old.strip() else text
        else:
            raise InvalidInputError("Choose append, replace or clear.")
        changes[field] = new
        message = f"{label} updated."

    if name == "get_current_setup" or name == "finish_setup":
        message = "Current setup." if name == "get_current_setup" else "Setup finished."
    elif name == "set_assessment_name":
        value = (arguments.get("name") or "").strip()
        changes["exam_name"] = value
        message = f"Assessment name set to '{value}'." if value else "Assessment name cleared."
    elif name == "set_course":
        course_id = arguments.get("course_id")
        if course_id:
            course = next((c for c in courses if c.id == course_id), None)
            if course is None:
                raise InvalidInputError("That isn't one of the professor's courses. Use an id from the course list.")
            changes["course_id"] = course.id
            message = f"Course set to {course.label}."
        else:
            changes["course_id"] = None
            message = "Course cleared."
    elif name == "set_duration":
        minutes = arguments.get("minutes")
        if minutes is not None and (not isinstance(minutes, int) or not 1 <= minutes <= LIMITS["duration_minutes"]):
            raise InvalidInputError("The duration must be a whole number of minutes from 1 to 1,440.")
        changes["duration_minutes"] = minutes
        message = f"Duration set to {minutes} minutes." if minutes else "Duration cleared."
    elif name == "set_question_distribution":
        mcq = arguments.get("mcq_percent")
        if mcq is None:
            changes.update(mcq_percentage=None, subjective_percentage=None)
            message = "Question format cleared."
        else:
            if not isinstance(mcq, int) or not 0 <= mcq <= 100:
                raise InvalidInputError("The multiple-choice share must be a whole number from 0 to 100.")
            changes.update(mcq_percentage=mcq, subjective_percentage=100 - mcq)
            message = f"Question format set to {mcq}% multiple choice and {100 - mcq}% subjective."
    elif name == "set_difficulty_distribution":
        easy, medium, hard = (arguments.get(key) for key in ("easy_percent", "medium_percent", "hard_percent"))
        problem = check_difficulty_distribution(easy, medium, hard)
        if problem:
            raise InvalidInputError(problem)
        changes.update(easy_percentage=easy, medium_percentage=medium, hard_percentage=hard)
        message = (
            f"Difficulty set to {easy}% easy, {medium}% medium and {hard}% hard."
            if easy is not None
            else "Difficulty distribution cleared."
        )
    elif name == "set_versions":
        count = arguments.get("count")
        if not isinstance(count, int) or not 1 <= count <= LIMITS["versions"]:
            raise InvalidInputError("The number of versions must be from 1 to 20.")
        changes["number_of_versions"] = count
        message = f"{count} version{'s' if count != 1 else ''}."
    elif name == "update_notes":
        text_update("additional_notes", "Notes")
    elif name == "update_professor_prompt":
        text_update("professor_prompt", "Instructions")
    else:
        raise InvalidInputError("Unknown tool.")

    previous = {key: getattr(current, key) for key in changes}
    updated = current.model_copy(update=changes)
    errors = updated.errors()
    if errors:
        raise InvalidInputError(next(iter(errors.values())))
    changed = {key: value for key, value in changes.items() if previous[key] != value}
    return updated, {
        "ok": True,
        "message": message,
        "changedFields": [_camel(key) for key in changed],
        # So the assistant can say "changed the duration from 60 to 90 minutes".
        "previousValues": {_camel(key): previous[key] for key in changed},
        "setup": describe_setup(updated, courses),
    }


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


class SetupAssistantService:
    def __init__(self, ai: OpenAIService, courses: list[CourseInfo], usage: Usage | None = None) -> None:
        self.ai = ai
        self.courses = courses
        self.usage = usage or Usage()

    async def chat(self, setup: AssessmentSetup, messages: list[dict[str, str]]) -> dict[str, Any]:
        """One text-chat turn: the model may call tools; the final setup comes back."""
        current = setup
        changed: list[str] = []
        finished = False

        async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            nonlocal current, finished
            try:
                current, result = execute_setup_tool(current, name, arguments, self.courses)
            except InvalidInputError as error:
                return {"ok": False, "error": error.message}
            changed.extend(result["changedFields"])
            finished = finished or name == "finish_setup"
            return result

        cleaned = [
            {"role": "user" if m.get("role") == "professor" else "assistant", "content": (m.get("content") or "")[:4000]}
            for m in messages[-16:]
            if (m.get("content") or "").strip()
        ]
        if not cleaned or cleaned[-1]["role"] != "user":
            raise InvalidInputError("Type a message first.")
        result = await self.ai.run_tool_loop(
            purpose="setup_chat",
            instructions=setup_chat_instructions(describe_setup(setup, self.courses), describe_courses(self.courses)),
            messages=cleaned,
            tools=SETUP_TOOLS,
            execute=execute,
            effort="low",
            usage=self.usage,
        )
        return {
            "reply": result.text or "Done.",
            "setup": current,
            "changedFields": list(dict.fromkeys(changed)),
            "finished": finished,
        }
