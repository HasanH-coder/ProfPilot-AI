"""'Build with AI': the professor and ProfPilot write the exam together, question by question.

Voice and text share one set of tools and one executor:

- in a voice call, the Realtime model picks a tool; the browser forwards the
  call to POST /api/exams/{id}/builder/tools/{name};
- in the text chat, the reasoning model picks tools in a loop on the server.

Either way the tool runs here, as the signed-in professor, with the same
validation and ownership checks. The conversational model only chooses what to
do; writing and revising questions is done by the reasoning model inside the
tools.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer, builder_chat_instructions, reviewer_instructions
from app.ai.tools import function_tool
from app.core.errors import AppError, ConflictError, InvalidInputError
from app.core.timing import timed
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import ReviewOutput
from app.services.exam_generation import ExamGenerationService, load_generation_context, suggest_next
from app.services.exam_store import ExamStore, FullExam, render_question
from app.services.jobs import RunContext, job_runner
from app.services.question_revision import PRESETS, QuestionRevisionService

logger = logging.getLogger("profpilot.builder")

_HISTORY_MESSAGES = 16

# Called with a tool's name and arguments just before it runs (the text chat's live status).
ToolStarted = Callable[[str, dict[str, Any]], Awaitable[None]]


_NUMBER = {"type": "integer", "description": "The question's number as shown in the exam preview (1, 2, 3…)."}

BUILDER_TOOLS: list[dict[str, Any]] = [
    function_tool("get_exam_state", "Get the current exam: its questions, their status, and progress against the plan.", {}),
    function_tool(
        "generate_next_question",
        "Write the next question and add it to the exam. Takes a little while. Leave question_type, difficulty, "
        "topic and points null unless the professor asked for them: ProfPilot then picks them so the exam keeps its "
        "planned mix, with difficulties and topics interleaved.",
        {
            "instructions": {"type": ["string", "null"], "description": "What the professor asked for, if anything."},
            "question_type": {
                "type": ["string", "null"],
                "enum": ["mcq", "short_answer", "long_answer", "problem", None],
                "description": "Only if the professor asked for this type.",
            },
            "difficulty": {
                "type": ["string", "null"],
                "enum": ["easy", "medium", "hard", None],
                "description": "Only if the professor asked for this difficulty (e.g. 'three easy questions first').",
            },
            "topic": {
                "type": ["string", "null"],
                "description": "Only if the professor named a topic or lecture (e.g. 'finish Lecture 3 first').",
            },
            "points": {"type": ["number", "null"], "description": "Only if the professor gave the marks."},
        },
    ),
    function_tool(
        "revise_question",
        "Change one question as the professor asks; its answer key is updated too. Takes a little while.",
        {
            "question_number": _NUMBER,
            "instruction": {"type": "string", "description": "The change, in the professor's words."},
            "quick_action": {
                "type": ["string", "null"],
                "enum": [*PRESETS, None],
                "description": (
                    "Use when the request matches one: harder, easier, application, clearer, shorter, alternative, replace."
                ),
            },
        },
    ),
    function_tool("approve_question", "Approve and lock a question the professor is happy with.", {"question_number": _NUMBER}),
    function_tool("unlock_question", "Unlock an approved question so it can change.", {"question_number": _NUMBER}),
    function_tool("delete_question", "Delete a question (only when the professor asks).", {"question_number": _NUMBER}),
    function_tool(
        "move_question",
        "Move a question to a new position.",
        {"question_number": _NUMBER, "new_position": {"type": "integer"}},
    ),
    function_tool(
        "review_question", "Check one question for problems (answer key, clarity, difficulty).", {"question_number": _NUMBER}
    ),
    function_tool("regenerate_solution", "Rewrite a question's answer key for its current text.", {"question_number": _NUMBER}),
    function_tool(
        "finish_exam",
        "Finish building: create the other versions and run the full quality check. Only when the professor is done.",
        {},
    ),
]


class ExamBuilderService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()
        self.store = ExamStore(db)

    # -- State -----------------------------------------------------------------------------

    async def state_summary(self, exam_id: str) -> str:
        full = await self.store.load(exam_id)
        return self._summary(full)

    def _summary(self, full: FullExam) -> str:
        spec = full.spec
        primary = full.primary_version
        questions = full.version_questions(primary["id"])
        summary = distribution.summarize(questions)
        lines = []
        if spec:
            title = spec.assessment_title or spec.assessment_type or "Untitled assessment"
            lines.append(f"Exam: {title}" + (f" ({spec.course})" if spec.course else "") + f". Versions: {spec.versions}.")
            targets = [
                f"format {spec.question_format.mcq_percent}% MCQ" if spec.question_format else None,
                (
                    f"difficulty {spec.difficulty.easy_percent}/{spec.difficulty.medium_percent}/"
                    f"{spec.difficulty.hard_percent} easy/medium/hard"
                    if spec.difficulty
                    else None
                ),
                f"{spec.duration_minutes} minutes" if spec.duration_minutes else None,
                (
                    f"{spec.question_count_min}–{spec.question_count_max} questions"
                    if spec.question_count_min and spec.question_count_max
                    else None
                ),
            ]
            lines.append("Targets (by marks): " + (", ".join(t for t in targets if t) or "none set") + ".")
            if spec.coverage:
                lines.append("Coverage: " + "; ".join(f"{c.topic} ({c.emphasis})" for c in spec.coverage[:12]) + ".")
        lines.append(f"Status: {full.exam['status']}. Questions so far (Version {primary['label']}):")
        for number, row in enumerate(questions, start=1):
            state = "approved" if row["status"] == "approved" else "draft"
            text = " ".join(row["prompt"].split())[:160]
            lines.append(f"{number}. [{row['type']}, {row['difficulty']}, {float(row['points']):g} pts, {state}] {text}")
        if not questions:
            lines.append("(none yet)")
        lines.append(
            f"Current split by marks: {summary['mcqPercent']:g}% MCQ; {summary['easyPercent']:g}/{summary['mediumPercent']:g}/"
            f"{summary['hardPercent']:g}% easy/medium/hard; about {summary['estimatedMinutes']:g} minutes; "
            f"{summary['totalPoints']:g} points."
        )
        if spec:
            suggestion = suggest_next(spec, questions)
            topic = f" on {suggestion['topic']}" if suggestion["topic"] else ""
            lines.append(
                f"Suggested next (keeps the planned mix): a {suggestion['difficulty']} "
                f"{suggestion['type'].replace('_', ' ')} question{topic}. ProfPilot uses this automatically when "
                "generate_next_question gets no type, difficulty or topic."
            )
        return "\n".join(lines)

    # -- Tools -----------------------------------------------------------------------------

    async def execute(self, exam_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Runs one tool for the professor. Returns a compact result for the model and the browser."""
        exam = await self.store.get_exam(exam_id)
        if exam["mode"] != "interactive":
            raise ConflictError("This exam wasn't created with Build with AI.", code="not_interactive")
        if exam["status"] == "generating":
            raise ConflictError("Wait until the exam has finished generating.", code="exam_generating")
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None or name not in {tool["name"] for tool in BUILDER_TOOLS}:
            raise InvalidInputError("Unknown tool.")
        try:
            async with timed(f"builder_tool.{name}", self.usage):
                return await handler(exam, arguments)
        except AppError as error:
            return {"ok": False, "error": error.message, "changedQuestionIds": []}

    async def _question_by_number(self, exam: dict[str, Any], arguments: dict[str, Any]) -> tuple[dict[str, Any], int]:
        full = await self.store.load(exam["id"])
        questions = full.version_questions(full.primary_version["id"])
        number = arguments.get("question_number")
        if not isinstance(number, int) or not 1 <= number <= len(questions):
            raise InvalidInputError(f"There is no question {number}. The exam has {len(questions)} question(s).")
        return questions[number - 1], number

    def _brief(self, row: dict[str, Any], number: int) -> dict[str, Any]:
        return {
            "number": number,
            "type": row["type"],
            "difficulty": row["difficulty"],
            "points": float(row["points"]),
            "status": row["status"],
            "text": render_question(row, number, include_key=False)[:1200],
        }

    async def _tool_get_exam_state(self, exam: dict[str, Any], _arguments: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "state": await self.state_summary(exam["id"]), "changedQuestionIds": []}

    async def _tool_generate_next_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        # Only what one new question needs: the stored plan context and the exam so far.
        ctx, full = await asyncio.gather(
            load_generation_context(self.db, exam["exam_project_id"], require_approved=False), self.store.load(exam["id"])
        )
        row = await ExamGenerationService(self.db, self.ai, self.usage).generate_one(
            ctx,
            exam["id"],
            instructions=arguments.get("instructions"),
            question_type=arguments.get("question_type"),
            difficulty=arguments.get("difficulty"),
            topic=arguments.get("topic"),
            points=arguments.get("points"),
            full=full,
        )
        number = await self.store.question_number(row)
        return {"ok": True, "question": self._brief(row, number), "changedQuestionIds": [row["id"]]}

    async def _tool_revise_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        preset = arguments.get("quick_action") if arguments.get("quick_action") in PRESETS else None
        result = await QuestionRevisionService(self.db, self.ai, self.usage).revise(
            row["id"], instruction=arguments.get("instruction"), preset=preset
        )
        return {
            "ok": True,
            "question": self._brief(result["question"], number),
            "changeSummary": result["changeSummary"],
            "distributionWarnings": [w["message"] for w in result.get("distributionWarnings", [])],
            "changedQuestionIds": [row["id"]],
        }

    async def _tool_approve_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        await QuestionRevisionService(self.db, self.ai, self.usage).set_approved(row["id"], True)
        return {"ok": True, "message": f"Question {number} is approved and locked.", "changedQuestionIds": [row["id"]]}

    async def _tool_unlock_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        await QuestionRevisionService(self.db, self.ai, self.usage).set_approved(row["id"], False)
        return {"ok": True, "message": f"Question {number} is unlocked.", "changedQuestionIds": [row["id"]]}

    async def _tool_delete_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        await QuestionRevisionService(self.db, self.ai, self.usage).delete(row["id"])
        return {"ok": True, "message": f"Question {number} was deleted.", "changedQuestionIds": [row["id"]]}

    async def _tool_move_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        full = await self.store.load(exam["id"])
        primary = full.primary_version
        ids = [q["id"] for q in full.version_questions(primary["id"])]
        target = arguments.get("new_position")
        if not isinstance(target, int) or not 1 <= target <= len(ids):
            raise InvalidInputError(f"Choose a position from 1 to {len(ids)}.")
        ids.remove(row["id"])
        ids.insert(target - 1, row["id"])
        await QuestionRevisionService(self.db, self.ai, self.usage).reorder(exam["id"], primary["id"], ids)
        return {"ok": True, "message": f"Question {number} is now question {target}.", "changedQuestionIds": [row["id"]]}

    async def _tool_review_question(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        ctx = await load_generation_context(self.db, exam["exam_project_id"], require_approved=False)
        framer = DataFramer()
        result = await self.ai.parse(
            purpose="review_question",
            instructions=reviewer_instructions(framer),
            input="\n\n".join(
                [
                    "APPROVED EXAM SPEC\n" + json.dumps(ctx.spec.model_dump(), indent=1),
                    "THE QUESTION TO REVIEW (with its answer key)\n"
                    + framer.block("question", f"question {number}", render_question(row, number)),
                    "Review only this question. Don't propose fixes; describe problems.",
                ]
            ),
            schema=ReviewOutput,
            effort="medium",
            max_output_tokens=12_000,
            usage=self.usage,
        )
        issues = [f"{issue.severity}: {issue.message}" for issue in result.issues]
        return {"ok": True, "summary": result.summary, "issues": issues, "changedQuestionIds": []}

    async def _tool_regenerate_solution(self, exam: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        row, number = await self._question_by_number(exam, arguments)
        result = await QuestionRevisionService(self.db, self.ai, self.usage).regenerate_solution(row["id"])
        return {"ok": True, "question": self._brief(result["question"], number), "changedQuestionIds": [row["id"]]}

    async def _tool_finish_exam(self, exam: dict[str, Any], _arguments: dict[str, Any]) -> dict[str, Any]:
        full = await self.store.load(exam["id"])
        if not full.version_questions(full.primary_version["id"]):
            raise ConflictError("Add at least one question before finishing.", code="empty_exam")
        run = await start_finish_job(self.db, self.ai, exam)
        return {
            "ok": True,
            "message": "Creating the other versions and running the quality check.",
            "runId": run["id"],
            "changedQuestionIds": [],
        }

    # -- Text chat -------------------------------------------------------------------------

    async def check_chat(self, exam_id: str, message: str) -> str:
        """The message to send, or a clear error, before any AI work starts."""
        exam = await self.store.get_exam(exam_id)
        if exam["mode"] != "interactive":
            raise ConflictError("This exam wasn't created with Build with AI.", code="not_interactive")
        message = message.strip()
        if not message:
            raise InvalidInputError("Type a message first.")
        return message

    async def chat(self, exam_id: str, message: str) -> dict[str, Any]:
        return await self.run_chat(exam_id, await self.check_chat(exam_id, message))

    async def run_chat(self, exam_id: str, message: str, on_tool: ToolStarted | None = None) -> dict[str, Any]:
        """One text-chat turn, for a message check_chat accepted. `on_tool` is told as
        soon as each tool starts, so the browser can show what is happening."""
        async with timed("builder_chat", self.usage):
            return await self._chat(exam_id, message, on_tool)

    async def _chat(self, exam_id: str, message: str, on_tool: ToolStarted | None) -> dict[str, Any]:
        await self.add_message(exam_id, "professor", message[:8000], channel="text")
        history = await self.db.select(
            "exam_builder_messages",
            columns="role, content",
            filters=[("exam_id", "eq", exam_id), ("professor_id", "eq", self.db.professor_id)],
            order=[("created_at", "desc")],
            limit=_HISTORY_MESSAGES,
        )
        messages = [
            {"role": "user" if row["role"] == "professor" else "assistant", "content": row["content"]}
            for row in reversed(history)
        ]
        changed: list[str] = []
        run_ids: list[str] = []

        async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if on_tool is not None:
                await on_tool(name, arguments)
            result = await self.execute(exam_id, name, arguments)
            changed.extend(result.get("changedQuestionIds") or [])
            if result.get("runId"):
                run_ids.append(result["runId"])
            return result

        result = await self.ai.run_tool_loop(
            purpose="builder_chat",
            instructions=builder_chat_instructions(await self.state_summary(exam_id)),
            messages=messages,
            tools=BUILDER_TOOLS,
            execute=execute,
            effort="low",
            usage=self.usage,
        )
        reply = result.text or "Done."
        await self.add_message(exam_id, "assistant", reply[:8000], channel="text")
        return {"reply": reply, "changedQuestionIds": list(dict.fromkeys(changed)), "runId": run_ids[-1] if run_ids else None}

    async def add_message(self, exam_id: str, role: str, content: str, *, channel: str) -> dict[str, Any]:
        content = content.strip()[:8000]
        if not content:
            raise InvalidInputError("The message is empty.")
        rows = await self.db.insert(
            "exam_builder_messages",
            {"exam_id": exam_id, "role": role, "content": content, "channel": channel},
            columns="id, role, channel, content, created_at",
        )
        return rows[0]

    async def messages(self, exam_id: str) -> list[dict[str, Any]]:
        await self.store.get_exam(exam_id)
        rows = await self.db.select(
            "exam_builder_messages",
            columns="id, role, channel, content, created_at",
            filters=[("exam_id", "eq", exam_id), ("professor_id", "eq", self.db.professor_id)],
            order=[("created_at", "asc")],
            limit=500,
        )
        return [
            {"id": r["id"], "role": r["role"], "channel": r["channel"], "content": r["content"], "createdAt": r["created_at"]}
            for r in rows
        ]


async def start_finish_job(db: Database, ai: OpenAIService, exam: dict[str, Any]) -> dict[str, Any]:
    """Creates the other versions and runs the quality check, as a background job."""

    async def work(run: RunContext) -> None:
        usage = run.usage
        ctx = await load_generation_context(db, exam["exam_project_id"], require_approved=False)
        store = ExamStore(db)
        full = await store.load(exam["id"])
        if len(full.versions) > 1:
            await run.stage("creating_versions")
            await ExamGenerationService(db, ai, usage).create_missing_variants(ctx, exam["id"])
        await run.stage("reviewing")
        from app.services.exam_review import ExamReviewService

        await ExamReviewService(db, ai, usage).review(exam["id"], ctx=ctx)
        await store.update_exam(exam["id"], {"status": "ready", "error_message": None})

    return await job_runner.start(
        db, assessment_id=exam["exam_project_id"], kind="versioning", work=work, exam_id=exam["id"], stage="starting"
    )
