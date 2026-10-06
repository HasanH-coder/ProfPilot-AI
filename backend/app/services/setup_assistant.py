"""'Set up with AI': tools that fill in the Assessment Setup form by conversation.

The executor is pure: it takes the form's current setup and a tool call, and
returns the new setup (or a clear error) using exactly the form's validation
rules. The browser shows the change immediately and saves it to the existing
draft. Voice (Realtime) and text chat use the same tools and the same executor.

Numbers the professor states are applied exactly as given, never adjusted to
make them fit: a difficulty split that doesn't add up to 100 comes back as a
short question for the assistant to ask. The conversation covers one setting
at a time; every result names the next setting to ask about, skipping those
already filled in or answered (set or skipped) in this conversation.
"""

from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import setup_chat_instructions
from app.ai.tools import function_tool
from app.core.errors import InvalidInputError
from app.core.timing import timed
from app.domain.setup import LIMITS, AssessmentSetup
from app.services.assessment_context import CourseInfo

_PERCENT = {"type": ["integer", "null"], "minimum": 0, "maximum": 100}
_TEXT_MODE = {"type": "string", "enum": ["append", "replace", "clear"]}

# The settings the assistant asks about, in order, with the one short question for each.
SETTING_QUESTIONS: dict[str, str] = {
    "course": "What course is this for?",
    "name": "What type of assessment is it?",
    "duration": "Do you want to specify a duration?",
    "question_format": "Do you want to specify the question format?",
    "difficulty": "Do you want to set a difficulty mix?",
    "versions": "How many versions do you need?",
    "coverage": "What should it cover?",
}
FINAL_QUESTION = "Anything else, or shall I finish?"

# The setting each tool answers.
_TOOL_SETTINGS = {
    "set_course": "course",
    "set_assessment_name": "name",
    "set_duration": "duration",
    "set_question_distribution": "question_format",
    "set_difficulty_distribution": "difficulty",
    "set_versions": "versions",
    "update_notes": "coverage",
}

SETUP_TOOLS: list[dict[str, Any]] = [
    function_tool("get_current_setup", "Read the assessment setup as it is on the professor's screen now.", {}),
    function_tool(
        "set_assessment_name",
        "Set the assessment's name or type (e.g. 'Midterm'), or null to clear it.",
        {"name": {"type": ["string", "null"]}},
    ),
    function_tool(
        "set_course",
        "Choose one of the professor's courses by id, or null for no course.",
        {"course_id": {"type": ["string", "null"]}},
    ),
    function_tool(
        "set_duration",
        "Set the duration in minutes, or null to clear it.",
        {"minutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 1440}},
    ),
    function_tool(
        "set_question_distribution",
        "Set the share of marks in multiple-choice questions, exactly as the professor said (the rest is "
        "subjective), or null to clear it.",
        {"mcq_percent": _PERCENT},
    ),
    function_tool(
        "set_difficulty_distribution",
        "Set the shares of marks that are easy, medium and hard, exactly as the professor said them; never adjust "
        "them to add up. All three must be given and add up to 100, or all null to clear the split.",
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
        "skip_setting",
        "The professor doesn't want to specify this setting ('skip', 'no', 'I don't care', 'leave it empty', "
        "'not now'). Nothing is changed and it isn't asked about again.",
        {"setting": {"type": "string", "enum": list(SETTING_QUESTIONS)}},
    ),
    function_tool(
        "finish_setup",
        "Call when the professor is done setting up (e.g. 'that's enough', 'finish', 'continue').",
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


def clean_addressed(addressed: list[str] | None) -> list[str]:
    """The settings already answered (set or skipped) in this conversation, in order."""
    return [setting for setting in dict.fromkeys(addressed or []) if setting in SETTING_QUESTIONS]


def next_setup_question(setup: AssessmentSetup, courses: list[CourseInfo], addressed: list[str] | None) -> str:
    """The one question to ask next: the first setting not yet filled in or answered."""
    done = set(clean_addressed(addressed))
    filled = {
        "course": setup.course_id is not None or not courses,
        "name": bool(setup.exam_name.strip()),
        "duration": setup.duration_minutes is not None,
        "question_format": setup.mcq_percentage is not None,
        "difficulty": setup.easy_percentage is not None,
        "versions": setup.number_of_versions != 1,
        "coverage": bool(setup.additional_notes.strip()),
    }
    for setting, question in SETTING_QUESTIONS.items():
        if setting not in done and not filled[setting]:
            return question
    return FINAL_QUESTION


def _number(value: Any, what: str) -> float | None:
    """A number as the model sent it: 30, 30.0, "30" or "30%". None stays None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise InvalidInputError(f"Give the {what} as a number.")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%").strip())
        except ValueError:
            pass
    raise InvalidInputError(f"Give the {what} as a number.")


def _whole(value: float, what: str) -> int:
    if abs(value - round(value)) > 1e-6:
        raise InvalidInputError(f"The {what} must be a whole number; ask the professor which whole number they want.")
    return int(round(value))


def _as_percent(values: list[float | None]) -> list[float | None]:
    """Shares written as fractions (0.3, 0.4, 0.3) are the same shares in percent."""
    given = [value for value in values if value is not None]
    if given and all(0 <= value <= 1 for value in given) and any(value != int(value) for value in given):
        return [None if value is None else value * 100 for value in values]
    return values


def difficulty_split(arguments: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """The professor's difficulty split, exactly as given, or an error the assistant asks about.

    Never normalized: 30/30/30 is not turned into 33/33/34; it comes back as
    "That adds up to 90%…" so the professor decides what the rest should be.
    """
    labels = ("easy", "medium", "hard")
    raw = _as_percent([_number(arguments.get(f"{label}_percent"), f"{label} share") for label in labels])
    if all(value is None for value in raw):
        return None, None, None
    given = {label: _whole(value, f"{label} share") for label, value in zip(labels, raw, strict=True) if value is not None}
    if any(not 0 <= value <= 100 for value in given.values()):
        raise InvalidInputError("Each share must be from 0 to 100%. Ask the professor to restate the split.")
    missing = [label for label in labels if label not in given]
    if missing:
        rest = 100 - sum(given.values())
        stated = " and ".join(f"{value}% {label}" for label, value in given.items())
        hint = f" (the rest would be {rest}%)" if len(missing) == 1 and 0 <= rest <= 100 else ""
        raise InvalidInputError(
            f"The professor gave {stated} but no {' or '.join(missing)} share. Nothing was changed. "
            f"Ask for it{hint}; don't assume it."
        )
    total = sum(given.values())
    if total != 100:
        stated = ", ".join(f"{given[label]}% {label}" for label in labels)
        question = (
            f"That adds up to {total}%. What should the remaining {100 - total}% be?"
            if total < 100
            else f"That adds up to {total}%. Which share should be lower?"
        )
        raise InvalidInputError(
            f"{stated} adds up to {total}%, not 100%. Nothing was changed. Ask the professor one short question, "
            f"e.g. '{question}' Don't change their numbers yourself."
        )
    return given["easy"], given["medium"], given["hard"]


def execute_setup_tool(
    setup: AssessmentSetup,
    name: str,
    arguments: dict[str, Any],
    courses: list[CourseInfo],
    addressed: list[str] | None = None,
) -> tuple[AssessmentSetup, dict[str, Any]]:
    """Applies one tool call. Returns the new setup and a result for the model."""
    current = setup.model_copy()
    changes: dict[str, Any] = {}
    message = ""
    answered = clean_addressed(addressed)

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
    elif name == "skip_setting":
        setting = arguments.get("setting")
        if setting not in SETTING_QUESTIONS:
            raise InvalidInputError("Choose one of the settings to skip.")
        answered.append(setting)
        message = "Left unspecified. Don't ask about it again unless the professor brings it up."
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
        value = _number(arguments.get("minutes"), "duration")
        minutes = None if value is None else _whole(value, "duration")
        if minutes is not None and not 1 <= minutes <= LIMITS["duration_minutes"]:
            raise InvalidInputError("The duration must be a whole number of minutes from 1 to 1,440.")
        changes["duration_minutes"] = minutes
        message = f"Duration set to {minutes} minutes." if minutes else "Duration cleared."
    elif name == "set_question_distribution":
        value = _as_percent([_number(arguments.get("mcq_percent"), "multiple-choice share")])[0]
        if value is None:
            changes.update(mcq_percentage=None, subjective_percentage=None)
            message = "Question format cleared."
        else:
            mcq = _whole(value, "multiple-choice share")
            if not 0 <= mcq <= 100:
                raise InvalidInputError("The multiple-choice share must be a whole number from 0 to 100.")
            changes.update(mcq_percentage=mcq, subjective_percentage=100 - mcq)
            message = f"Question format set to {mcq}% multiple choice and {100 - mcq}% subjective."
    elif name == "set_difficulty_distribution":
        easy, medium, hard = difficulty_split(arguments)
        changes.update(easy_percentage=easy, medium_percentage=medium, hard_percentage=hard)
        message = (
            f"Difficulty set to {easy}% easy, {medium}% medium and {hard}% hard."
            if easy is not None
            else "Difficulty distribution cleared."
        )
    elif name == "set_versions":
        value = _number(arguments.get("count"), "number of versions")
        count = None if value is None else _whole(value, "number of versions")
        if count is None or not 1 <= count <= LIMITS["versions"]:
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
    if name in _TOOL_SETTINGS:
        answered.append(_TOOL_SETTINGS[name])
    answered = clean_addressed(answered)
    return updated, {
        "ok": True,
        "message": message,
        "changedFields": [_camel(key) for key in changed],
        # So the assistant can say "changed the duration from 60 to 90 minutes".
        "previousValues": {_camel(key): previous[key] for key in changed},
        "setup": describe_setup(updated, courses),
        # Settings answered in this conversation (set or skipped), and the one question to ask next.
        "addressed": answered,
        "nextQuestion": next_setup_question(updated, courses, answered),
    }


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


class SetupAssistantService:
    def __init__(self, ai: OpenAIService, courses: list[CourseInfo], usage: Usage | None = None) -> None:
        self.ai = ai
        self.courses = courses
        self.usage = usage or Usage()

    async def chat(
        self, setup: AssessmentSetup, messages: list[dict[str, str]], addressed: list[str] | None = None
    ) -> dict[str, Any]:
        """One text-chat turn: the model may call tools; the final setup comes back."""
        current = setup
        changed: list[str] = []
        finished = False
        answered = clean_addressed(addressed)

        async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            nonlocal current, finished, answered
            try:
                current, result = execute_setup_tool(current, name, arguments, self.courses, answered)
            except InvalidInputError as error:
                return {"ok": False, "error": error.message}
            changed.extend(result["changedFields"])
            answered = result["addressed"]
            finished = finished or name == "finish_setup"
            return result

        cleaned = [
            {"role": "user" if m.get("role") == "professor" else "assistant", "content": (m.get("content") or "")[:4000]}
            for m in messages[-16:]
            if (m.get("content") or "").strip()
        ]
        if not cleaned or cleaned[-1]["role"] != "user":
            raise InvalidInputError("Type a message first.")
        async with timed("setup_chat", self.usage):
            result = await self.ai.run_tool_loop(
                purpose="setup_chat",
                instructions=setup_chat_instructions(
                    describe_setup(setup, self.courses),
                    describe_courses(self.courses),
                    next_setup_question(setup, self.courses, answered),
                ),
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
            "addressed": answered,
        }
