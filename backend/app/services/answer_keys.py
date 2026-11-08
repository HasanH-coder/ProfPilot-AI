"""Answer keys, written only when the professor asks: for one question, or for every question still
without an up-to-date key.

Questions are written without their keys, so the professor can review and edit them first. A key is then
written from the question exactly as it is saved (its wording, choices and parts after the professor's
edits), with its slides, the case it builds on and the course material, and checked in code before it is
saved. A key whose question changed while it was being written is not saved: it would answer the old
wording. A question changed after its key was written keeps that key, marked out of date
(needs_solution_review), until the key is written again or the professor fixes it by hand.
"""

import logging
from typing import Any

from app.ai.openai_service import OpenAIService, Usage, gather_limited
from app.ai.prompts import DataFramer, answer_key_instructions
from app.core.config import settings
from app.core.errors import AIServiceError, AppError, ConflictError
from app.db.supabase import Database
from app.schemas.ai import AnswerKeyBatch, QuestionKey
from app.services.document_retrieval import DocumentRetrievalService, assign_refs, render_excerpts
from app.services.exam_generation import GenerationContext, load_generation_context, question_effort
from app.services.exam_store import ExamStore, FullExam, has_answer_key, render_question, row_to_content
from app.services.jobs import RunContext
from app.services.slides import WRITER_IMAGES, image_parts, question_slides, render_question_sources, slide_images

logger = logging.getLogger("profpilot.answer_keys")

# Keys written together in one request.
_BATCH_SIZE = 4
_EFFORTS = ("low", "medium", "high")
_POINTS_TOLERANCE = 0.01


def needs_key(row: dict[str, Any]) -> bool:
    """A question without an answer key, or whose key was written before the question last changed."""
    return not has_answer_key(row) or bool(row["needs_solution_review"])


def _label(text: str) -> str:
    return text.strip().strip("()").lower()


def key_problems(batch: AnswerKeyBatch, rows: list[dict[str, Any]]) -> list[str]:
    """What keeps a batch of keys from being saved: each key must fit its question as saved."""
    problems: list[str] = []
    by_number = {key.number: key for key in batch.keys}
    numbers = list(range(1, len(rows) + 1))
    if sorted(by_number) != numbers or len(batch.keys) != len(rows):
        problems.append("Write exactly one key for each question, numbered " + ", ".join(map(str, numbers)) + ".")
    for number, row in enumerate(rows, start=1):
        key = by_number.get(number)
        if key is None:
            continue
        label = f"Question {number}'s key"
        if not key.answer.strip():
            problems.append(f"{label} has no answer.")
        if row["type"] == "mcq":
            ids = [choice["id"] for choice in row.get("choices") or []]
            if (key.correct_choice or "").strip().upper() not in ids:
                problems.append(f"{label}: correct_choice must be one of the question's choice ids ({', '.join(ids)}).")
            continue
        parts = row.get("subparts") or []
        points = float(row["points"])
        if parts:
            expected = [part["label"] for part in parts]
            given = key.subparts or []
            if [_label(part.label) for part in given] != [_label(label) for label in expected]:
                problems.append(f"{label} needs one entry in subparts per part, in order: {', '.join(expected)}.")
                continue
            for part, part_key in zip(parts, given, strict=True):
                part_points = float(part["points"])
                if not part_key.answer.strip():
                    problems.append(f"{label}: part ({part['label']}) has no answer.")
                if abs(sum(item.points for item in part_key.rubric) - part_points) > _POINTS_TOLERANCE:
                    problems.append(f"{label}: part ({part['label']})'s rubric must add up to {part_points:g} points.")
        else:
            if not key.solution.strip():
                problems.append(f"{label} needs a worked solution or model answer.")
            if not key.rubric or abs(sum(item.points for item in key.rubric) - points) > _POINTS_TOLERANCE:
                problems.append(f"{label}'s rubric must add up to {points:g} points.")
    return problems


def key_values(row: dict[str, Any], key: QuestionKey) -> dict[str, Any]:
    """The columns a checked key sets on its question. A problem the key's writer found with the question
    itself stays with the key, where the professor reads it."""
    explanation = (key.explanation or "").strip()
    notes = (key.notes or "").strip()
    if notes:
        explanation = "\n\n".join(part for part in [explanation, f"Check: {notes}"] if part)
    values: dict[str, Any] = {
        "answer": key.answer.strip(),
        "solution": key.solution.strip() or None,
        "explanation": explanation or None,
        "needs_solution_review": False,
    }
    if row["type"] == "mcq":
        values["correct_choice"] = (key.correct_choice or "").strip().upper()
        values["rubric"] = None
        return values
    parts = row.get("subparts") or []
    if parts:
        values["subparts"] = [
            {**part, "answer": part_key.answer.strip(), "rubric": [item.model_dump() for item in part_key.rubric]}
            for part, part_key in zip(parts, key.subparts or [], strict=True)
        ]
        values["rubric"] = [item.model_dump() for item in key.rubric] if key.rubric else None
    else:
        values["rubric"] = [item.model_dump() for item in key.rubric or []]
    return values


def _formula_problems(ctx: GenerationContext, rows: list[dict[str, Any]], batch: AnswerKeyBatch) -> list[str]:
    """Keys that quote a lecture formula whose extracted text is garbled (checked once the keys fit)."""
    keys = {key.number: key for key in batch.keys}
    return ctx.formula_problems(
        (f"Question {number}'s key", row_to_content({**row, **key_values(row, keys[number])}))
        for number, row in enumerate(rows, start=1)
    )


def _effort(rows: list[dict[str, Any]]) -> Any:
    return max((question_effort(row["type"], row["difficulty"]) for row in rows), key=_EFFORTS.index)


class AnswerKeyService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()
        self.store = ExamStore(db)

    async def write_one(self, question_id: str) -> dict[str, Any]:
        """Writes (or writes again) one question's answer key, from the question as it is saved. An approved
        question gets a key it lacks, or one that is out of date; its current key stays until it is unlocked."""
        question = await self.store.get_question(question_id)
        exam = await self.store.require_editable(question)
        if question["status"] == "approved" and not needs_key(question):
            raise ConflictError(
                "This question is approved and its answer key is up to date. Unlock it to write the key again.",
                code="question_locked",
            )
        full = await self.store.load(exam["id"])
        ctx = await load_generation_context(self.db, exam["exam_project_id"], require_approved=False)
        saved = await self._write(ctx, full, [question])
        if not saved:
            raise ConflictError(
                "The question changed while its answer key was being written. Try again.", code="question_changed"
            )
        row, notes = saved[0]
        return {"question": row, "notes": notes}

    async def write_missing(self, exam_id: str, run: RunContext | None = None) -> dict[str, int]:
        """Writes the key of every question, in every version, that has none or an out-of-date one. Up-to-date
        keys are kept. Each batch is saved as soon as it is written, so the page shows the keys as they come."""
        full = await self.store.load(exam_id)
        rows = [row for version in full.versions for row in full.version_questions(version["id"]) if needs_key(row)]
        if not rows:
            return {"written": 0, "failed": 0}
        ctx = await load_generation_context(self.db, full.exam["exam_project_id"], require_approved=False)
        batches = [rows[index : index + _BATCH_SIZE] for index in range(0, len(rows), _BATCH_SIZE)]
        failed = 0

        async def write(batch: list[dict[str, Any]]) -> int:
            nonlocal failed
            try:
                saved = await self._write(ctx, full, batch)
            except AIServiceError:
                # The other keys are still written; these stay missing, for the next try.
                logger.info("A batch of %d answer keys couldn't be written", len(batch))
                failed += len(batch)
                return 0
            if run is not None:
                await run.heartbeat()
            return len(saved)

        written = sum(await gather_limited(settings.max_concurrent_generation_calls, [write(batch) for batch in batches]))
        if failed:
            raise AppError(
                f"{failed} answer key(s) couldn't be written. The {written} written are kept: press Generate all "
                "answer keys again to write the rest.",
                code="answer_keys_incomplete",
            )
        return {"written": written, "failed": 0}

    async def _write(
        self, ctx: GenerationContext, full: FullExam, rows: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], str | None]]:
        """Writes the keys of these questions in one request and saves each one whose question is unchanged."""
        framer = DataFramer()
        slides = ctx.slides
        cited = question_slides(rows, slides)
        images = await slide_images(
            self.db, slides, [ref for _, refs in cited for ref in refs], limit=WRITER_IMAGES, files=ctx.files
        )
        excerpts = assign_refs(
            await DocumentRetrievalService(self.db, self.ai, self.usage).search(
                ctx.record.id,
                [row["prompt"][:400] for row in rows],
                document_names=ctx.document_names,
                per_query=3,
                max_excerpts=8,
                max_chars=14_000,
            )
        )
        sections = {section["id"]: section for section in full.sections}
        listing = []
        for number, row in enumerate(rows, start=1):
            section = sections.get(row.get("section_id"))
            case = (
                f"(Question {number} builds on this case, which students read first: {section['title']}\n"
                f"{section['instructions']})\n"
                if section and section.get("instructions")
                else ""
            )
            listing.append(case + render_question(row, number, include_key=False))
        batch = await self.ai.parse_with_files(
            purpose="write_answer_keys",
            instructions=answer_key_instructions(framer),
            text="\n\n".join(
                part
                for part in [
                    "APPROVED BRIEF\n" + ctx.enhanced_prompt,
                    render_question_sources(cited, slides, framer),
                    render_excerpts(excerpts, framer, title="RELEVANT COURSE MATERIAL"),
                    "THE QUESTIONS, AS THE PROFESSOR SAVED THEM (application data)\n"
                    + framer.block("questions", "questions", "\n\n".join(listing)),
                    "Write their answer keys now.",
                ]
                if part
            ),
            files=image_parts(images, slides),
            schema=AnswerKeyBatch,
            effort=_effort(rows),
            max_output_tokens=24_000,
            validate=lambda result: key_problems(result, rows) or _formula_problems(ctx, rows, result),
            usage=self.usage,
        )
        keys = {key.number: key for key in batch.keys}
        saved: list[tuple[dict[str, Any], str | None]] = []
        for number, row in enumerate(rows, start=1):
            key = keys[number]
            values = key_values(row, key)
            if has_answer_key(row):
                # The key it replaces stays in the question's history.
                updated = await self.store.change_question(
                    row, values, source="solution_regenerated", instruction="Answer key written again"
                )
            else:
                updated = await self.store.update_if_unchanged(row, values)
            if updated is not None:
                saved.append((updated, (key.notes or "").strip() or None))
        return saved
