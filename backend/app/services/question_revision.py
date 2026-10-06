"""Changing one question at a time, by hand or with AI, with its answer key kept in sync.

Every change records the question as it was before (question_revisions), so
the professor can see what changed and restore an earlier version. Approved
questions are locked: nothing changes them until the professor unlocks them.
"""

import asyncio
import re
from typing import Any

from app.ai.openai_service import Effort, OpenAIService, Usage
from app.ai.prompts import DataFramer, reviser_instructions, solution_instructions
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.timing import timed
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import QuestionContent, RevisedQuestion, SolutionKey
from app.schemas.common import ApiModel, parse_id
from app.services.document_retrieval import DocumentRetrievalService, assign_refs, render_excerpts
from app.services.exam_generation import (
    GenerationContext,
    excerpt_ref_map,
    load_generation_context,
    question_effort,
)
from app.services.exam_store import (
    SNAPSHOT_FIELDS,
    ExamStore,
    content_to_row,
    normalize_content,
    question_problems,
    render_question,
    row_to_content,
    similar,
)
from app.services.personalization import PreferenceService

LOCKED_MESSAGE = "This question is approved and locked. Unlock it to change it."
_LEVELS = ["easy", "medium", "hard"]

# Quick actions in the editor. Each is an instruction plus, for harder/easier,
# the difficulty the question should end up at.
PRESETS: dict[str, dict[str, Any]] = {
    "harder": {
        "label": "Make harder",
        "instruction": "Make this question harder by raising its cognitive demand (more reasoning steps, synthesis, "
        "transfer to a less familiar situation, or subtler distinctions), not by making it longer. Keep the same topic "
        "and question type.",
        "shift": 1,
    },
    "easier": {
        "label": "Make easier",
        "instruction": "Make this question easier: fewer steps or clearer cues, while still testing the same topic. "
        "Keep the same question type.",
        "shift": -1,
    },
    "application": {
        "label": "More application-based",
        "instruction": "Make this question more application-based: have students use the concept in a realistic "
        "scenario or problem rather than recall it. Keep the topic, question type, difficulty and points.",
    },
    "clearer": {
        "label": "Make clearer",
        "instruction": "Make this question clearer and unambiguous without changing what it tests, its difficulty, "
        "type or points.",
    },
    "shorter": {
        "label": "Shorten",
        "instruction": "Shorten this question so it needs less reading, keeping what it tests, its difficulty, type and points.",
    },
    "alternative": {
        "label": "Generate alternative",
        "instruction": "Write a different question that tests the same learning objective, with the same type, "
        "difficulty and points.",
    },
    "replace": {
        "label": "Replace question",
        "instruction": "Replace this question with a new one on a different aspect of the course material that this "
        "exam covers, with the same type, difficulty and points, and not duplicating other questions.",
    },
}


class QuestionEdit(ApiModel):
    """A manual edit. Only the fields sent are changed."""

    prompt: str | None = None
    type: str | None = None
    difficulty: str | None = None
    points: float | None = None
    choices: list[dict[str, Any]] | None = None
    correct_choice: str | None = None
    subparts: list[dict[str, Any]] | None = None
    answer: str | None = None
    solution: str | None = None
    rubric: list[dict[str, Any]] | None = None
    explanation: str | None = None
    section_id: str | None = None
    estimated_minutes: float | None = None


_CONTENT_FIELDS = {"prompt", "type", "choices", "subparts", "points"}
_KEY_FIELDS = {"answer", "solution", "correct_choice", "rubric"}


class QuestionRevisionService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()
        self.store = ExamStore(db)

    # -- AI revision -------------------------------------------------------------------

    async def revise(self, question_id: str, *, instruction: str | None, preset: str | None) -> dict[str, Any]:
        async with timed("revise_question", self.usage):
            return await self._revise(question_id, instruction=instruction, preset=preset)

    async def _revise(self, question_id: str, *, instruction: str | None, preset: str | None) -> dict[str, Any]:
        """Revises one question and its answer key. Only that question's context is
        loaded: no file is read again, no other question changes, no exam review runs."""
        question = await self.store.get_question(question_id)
        exam = await self.store.require_editable(question)
        if question["status"] == "approved":
            raise ConflictError(LOCKED_MESSAGE, code="question_locked")
        if preset and preset not in PRESETS:
            raise InvalidInputError("Unknown quick action.")
        instruction = (instruction or "").strip()
        if not preset and not instruction:
            raise InvalidInputError("Tell ProfPilot how to change the question.")

        target_difficulty = question["difficulty"]
        parts: list[str] = []
        if preset:
            parts.append(PRESETS[preset]["instruction"])
            shift = PRESETS[preset].get("shift", 0)
            if shift:
                index = min(max(_LEVELS.index(question["difficulty"]) + shift, 0), 2)
                target_difficulty = _LEVELS[index]
                if target_difficulty == question["difficulty"]:
                    parts.append(f"It is already {target_difficulty}; adjust within that level.")
                else:
                    parts.append(f"Its difficulty becomes {target_difficulty}.")
        if instruction:
            parts.append("The professor's instruction: " + instruction)
        request = " ".join(parts)

        ctx, full = await asyncio.gather(
            load_generation_context(self.db, exam["exam_project_id"], require_approved=False), self.store.load(exam["id"])
        )
        number = _number_of(full, question)
        others = [q["prompt"] for q in full.version_questions(question["version_id"]) if q["id"] != question["id"]]
        framer = DataFramer()
        queries = [*(question.get("concepts") or [])[:3], instruction or question["prompt"][:400]]
        excerpts = assign_refs(
            await DocumentRetrievalService(self.db, self.ai, self.usage).search(
                ctx.record.id, queries, document_names=ctx.document_names, per_query=4, max_excerpts=8, max_chars=14_000
            )
        )
        keeps_type = bool(preset)
        keeps_points = bool(preset)

        def problems(result: RevisedQuestion) -> list[str]:
            content = normalize_content(result.question)
            found = question_problems(content)
            if keeps_type and content.type != question["type"]:
                found.append(f"Keep the question type {question['type']}.")
            if keeps_points and abs(content.points - float(question["points"])) > 0.01:
                found.append(f"Keep the question worth {float(question['points']):g} points.")
            if preset in ("harder", "easier") and content.difficulty != target_difficulty:
                found.append(f"Set difficulty to {target_difficulty}.")
            if content.figure_document_id and content.figure_document_id not in ctx.figure_ids:
                found.append("figure_document_id must be null or one of the listed image attachments.")
            if preset in ("alternative", "replace") and similar(content.prompt, question["prompt"]) > 0.8:
                found.append("Write a genuinely different question, not a reworded one.")
            if any(similar(content.prompt, other) > 0.85 for other in others):
                found.append("The revised question duplicates another question in the exam.")
            return found

        result = await self.ai.parse(
            purpose="revise_question",
            instructions=reviser_instructions(framer),
            input="\n\n".join(
                [
                    *ctx.common_sections(framer),
                    "OTHER QUESTIONS IN THIS VERSION (don't duplicate them)\n"
                    + ("\n".join(f"- {text[:200]}" for text in others) or "(none)"),
                    render_excerpts(excerpts, framer),
                    "THE QUESTION TO REVISE (application data)\n"
                    + framer.block("question", f"question {number}", render_question(question, number)),
                    "REQUESTED CHANGE\n" + request,
                ]
            ),
            schema=RevisedQuestion,
            effort=_revision_effort(question["type"], target_difficulty, instruction),
            max_output_tokens=20_000,
            validate=problems,
            usage=self.usage,
        )
        content = normalize_content(result.question)
        label = f"[{preset}] " if preset else ""
        await self.store.record_revision(
            question,
            source="ai_revision",
            instruction=(label + (instruction or PRESETS.get(preset or "", {}).get("label", ""))).strip(),
        )
        updated = await self.store.update_question(
            question["id"],
            {
                **content_to_row(content, excerpt_refs=excerpt_ref_map(excerpts), allowed_figures=ctx.figure_ids),
                "status": "draft",
            },
        )
        return {"question": updated, "changeSummary": result.change_summary, **await self._version_warnings(updated, ctx)}

    # -- Answer key ----------------------------------------------------------------------

    async def regenerate_solution(self, question_id: str) -> dict[str, Any]:
        question = await self.store.get_question(question_id)
        exam = await self.store.require_editable(question)
        if question["status"] == "approved":
            raise ConflictError(LOCKED_MESSAGE, code="question_locked")
        ctx = await load_generation_context(self.db, exam["exam_project_id"], require_approved=False)
        framer = DataFramer()
        excerpts = assign_refs(
            await DocumentRetrievalService(self.db, self.ai, self.usage).search(
                ctx.record.id,
                [question["prompt"][:800]],
                document_names=ctx.document_names,
                per_query=6,
                max_excerpts=6,
                max_chars=10_000,
            )
        )
        choice_ids = [choice["id"] for choice in question.get("choices") or []]
        subparts = question.get("subparts") or []
        points = float(question["points"])

        def problems(key: SolutionKey) -> list[str]:
            found = []
            if question["type"] == "mcq" and (key.correct_choice or "").upper() not in choice_ids:
                found.append(f"correct_choice must be one of {', '.join(choice_ids)}.")
            if subparts and len(key.subpart_answers or []) != len(subparts):
                found.append(f"Give exactly {len(subparts)} subpart answers, in order.")
            if question["type"] != "mcq" and not subparts:
                if not key.rubric or abs(sum(item.points for item in key.rubric) - points) > 0.01:
                    found.append(f"The rubric must add up to {points:g} points.")
            if not key.answer.strip():
                found.append("Give the answer.")
            return found

        number = 1
        key = await self.ai.parse(
            purpose="regenerate_solution",
            instructions=solution_instructions(framer),
            input="\n\n".join(
                [
                    render_excerpts(excerpts, framer),
                    "THE QUESTION (as the professor edited it)\n"
                    + framer.block("question", "question", render_question(question, number, include_key=False)),
                ]
            ),
            schema=SolutionKey,
            effort=question_effort(question["type"], question["difficulty"]),
            max_output_tokens=16_000,
            validate=problems,
            usage=self.usage,
        )
        values: dict[str, Any] = {
            "answer": key.answer.strip(),
            "solution": key.solution.strip() or None,
            "explanation": key.explanation,
            "needs_solution_review": False,
        }
        if question["type"] == "mcq":
            values["correct_choice"] = (key.correct_choice or "").upper()
        else:
            values["rubric"] = [item.model_dump() for item in key.rubric] if key.rubric else None
        if subparts:
            values["subparts"] = [
                {**part, "answer": answer} for part, answer in zip(subparts, key.subpart_answers or [], strict=False)
            ]
        await self.store.record_revision(question, source="solution_regenerated", instruction="Answer key regenerated")
        updated = await self.store.update_question(question["id"], values)
        return {"question": updated, "notes": key.notes, **await self._version_warnings(updated, ctx)}

    # -- Manual edits ----------------------------------------------------------------------

    async def edit(self, question_id: str, edit: QuestionEdit) -> dict[str, Any]:
        question = await self.store.get_question(question_id)
        exam = await self.store.require_editable(question)
        if question["status"] == "approved":
            raise ConflictError(LOCKED_MESSAGE, code="question_locked")
        sent = {name for name in edit.model_fields_set}
        if not sent:
            raise InvalidInputError("Nothing to change.")
        changes = {name: getattr(edit, name) for name in sent}
        if "section_id" in changes and changes["section_id"] is not None:
            changes["section_id"] = parse_id(changes["section_id"], what="section")
            sections = await self.db.select(
                "exam_sections",
                columns="id",
                filters=[
                    ("exam_id", "eq", exam["id"]),
                    ("id", "eq", changes["section_id"]),
                    ("professor_id", "eq", self.db.professor_id),
                ],
            )
            if not sections:
                raise InvalidInputError("Choose one of this exam's sections.")
        merged = {**question, **changes}
        if merged.get("type") != "mcq":
            merged["choices"], merged["correct_choice"] = None, None
        try:
            content = QuestionContent.model_validate(
                {
                    **row_to_content(question).model_dump(),
                    **{
                        key: merged.get(key)
                        for key in ("type", "difficulty", "points", "prompt", "choices", "correct_choice", "subparts", "rubric")
                    },
                    "answer": merged.get("answer") or "",
                    "solution": merged.get("solution") or "",
                    "explanation": merged.get("explanation"),
                    "estimated_minutes": float(merged.get("estimated_minutes") or question.get("estimated_minutes") or 1),
                }
            )
        except ValueError as error:
            raise InvalidInputError("Some of the question's fields aren't valid.") from error
        content = normalize_content(content)
        integrity = question_problems(content, manual=True)
        if integrity:
            raise InvalidInputError(integrity[0])

        content_changed = any(name in sent and changes[name] != question.get(name) for name in _CONTENT_FIELDS)
        key_changed = any(name in sent and changes[name] != question.get(name) for name in _KEY_FIELDS)
        needs_review = question["needs_solution_review"]
        if content_changed and not key_changed:
            needs_review = True
        elif key_changed:
            needs_review = bool(question_problems(content))

        values: dict[str, Any] = {
            "type": content.type,
            "difficulty": content.difficulty,
            "points": content.points,
            "prompt": content.prompt,
            "choices": [choice.model_dump() for choice in content.choices] if content.choices else None,
            "correct_choice": content.correct_choice if content.type == "mcq" else None,
            "subparts": [part.model_dump() for part in content.subparts] if content.subparts else None,
            "answer": content.answer or None,
            "solution": content.solution or None,
            "rubric": [item.model_dump() for item in content.rubric] if content.rubric else None,
            "explanation": content.explanation,
            "estimated_minutes": round(content.estimated_minutes, 1),
            "needs_solution_review": needs_review,
        }
        if "section_id" in sent:
            values["section_id"] = changes["section_id"]
        await self.store.record_revision(question, source="manual_edit", instruction="Edited by the professor")
        updated = await self.store.update_question(question["id"], values)
        ctx = await load_generation_context(self.db, exam["exam_project_id"], require_approved=False)
        return {"question": updated, **await self._version_warnings(updated, ctx)}

    # -- History -----------------------------------------------------------------------------

    async def restore(self, question_id: str, revision_id: str) -> dict[str, Any]:
        question = await self.store.get_question(question_id)
        await self.store.require_editable(question)
        if question["status"] == "approved":
            raise ConflictError(LOCKED_MESSAGE, code="question_locked")
        revision_id = parse_id(revision_id, what="revision")
        revision = await self.db.select_one(
            "question_revisions",
            columns="id, revision_number, snapshot",
            filters=[
                ("id", "eq", revision_id),
                ("question_id", "eq", question["id"]),
                ("professor_id", "eq", self.db.professor_id),
            ],
        )
        if revision is None:
            raise NotFoundError("This revision doesn't exist.")
        snapshot = {name: revision["snapshot"].get(name) for name in SNAPSHOT_FIELDS if name in revision["snapshot"]}
        await self.store.record_revision(
            question, source="restore", instruction=f"Restored revision {revision['revision_number']}"
        )
        updated = await self.store.update_question(question["id"], {**snapshot, "status": "draft"})
        return {"question": updated}

    # -- Status, deletion, order -------------------------------------------------------------

    async def set_approved(self, question_id: str, approved: bool) -> dict[str, Any]:
        question = await self.store.get_question(question_id)
        await self.store.require_editable(question)
        updated = await self.store.update_question(question["id"], {"status": "approved" if approved else "draft"})
        if approved:
            await PreferenceService(self.db).record_accepted_revision(updated)
        return {"question": updated}

    async def delete(self, question_id: str) -> None:
        """Deletes the question from every version, so versions stay equivalent."""
        question = await self.store.get_question(question_id)
        await self.store.require_editable(question)
        twins = await self.db.select(
            "exam_questions",
            columns="id, status",
            filters=[
                ("exam_id", "eq", question["exam_id"]),
                ("slot_id", "eq", question["slot_id"]),
                ("professor_id", "eq", self.db.professor_id),
            ],
        )
        if any(twin["status"] == "approved" for twin in twins):
            raise ConflictError("Unlock this question (in every version) before deleting it.", code="question_locked")
        for twin in twins:
            await self.store.delete_question(twin["id"])

    async def reorder(self, exam_id: str, version_id: str, question_ids: list[str]) -> None:
        exam = await self.store.get_exam(exam_id)
        if exam["status"] == "generating":
            raise ConflictError("Wait until the exam has finished generating.", code="exam_generating")
        version_id = parse_id(version_id, what="version")
        ids = [parse_id(question_id, what="question") for question_id in question_ids]
        await self.db.rpc("reorder_exam_questions", {"p_version_id": version_id, "p_question_ids": ids})

    async def sync_variants(self, question_id: str) -> dict[str, Any]:
        """Rewrites this question's equivalents in the other versions to match it."""
        from app.services.exam_generation import ExamGenerationService

        question = await self.store.get_question(question_id)
        exam = await self.store.require_editable(question)
        full = await self.store.load(exam["id"])
        if len(full.versions) < 2:
            raise ConflictError("This exam has only one version.", code="single_version")
        ctx = await load_generation_context(self.db, exam["exam_project_id"], require_approved=False)
        generation = ExamGenerationService(self.db, self.ai, self.usage)
        changed: list[dict[str, Any]] = []
        skipped: list[str] = []
        for version in full.versions:
            if version["id"] == question["version_id"]:
                continue
            twin = next((q for q in full.version_questions(version["id"]) if q["slot_id"] == question["slot_id"]), None)
            if twin is not None and twin["status"] == "approved":
                skipped.append(version["label"])
                continue
            source_label = next(v["label"] for v in full.versions if v["id"] == question["version_id"])
            variants = await generation.write_variants(ctx, full, [question], version["label"], source_label=source_label)
            content = variants[0][1]
            values = {**content_to_row(content, allowed_figures=ctx.figure_ids), "source_refs": question.get("source_refs") or []}
            if twin is None:
                row = await self.store.insert_question(
                    exam["id"],
                    version["id"],
                    {
                        **values,
                        "position": question["position"],
                        "section_id": question.get("section_id"),
                        "slot_id": question["slot_id"],
                    },
                )
            else:
                await self.store.record_revision(
                    twin, source="ai_revision", instruction="Matched to the edited question in another version"
                )
                row = await self.store.update_question(twin["id"], {**values, "status": "draft"})
            changed.append(row)
        return {"questions": changed, "skippedVersions": skipped}

    async def _version_warnings(self, question: dict[str, Any], ctx: GenerationContext) -> dict[str, Any]:
        rows = await self.db.select(
            "exam_questions",
            columns="type, difficulty, points, estimated_minutes",
            filters=[("version_id", "eq", question["version_id"]), ("professor_id", "eq", self.db.professor_id)],
        )
        summary = distribution.summarize(rows)
        return {"distribution": summary, "distributionWarnings": distribution.compare(summary, ctx.spec)}


_DEMANDING = re.compile(
    r"\b(hard|harder|hardest|difficult|challeng\w*|complex|multi[- ]?step|proof|prove|derive|derivation)\b", re.I
)


def _revision_effort(question_type: str, target_difficulty: str, instruction: str) -> Effort:
    """Medium reasoning for a normal revision; high when the result is hard or a
    multi-step problem, or the professor's own instruction asks for something demanding."""
    effort = question_effort(question_type, target_difficulty)
    if effort == "medium" and instruction and (_DEMANDING.search(instruction) or len(instruction) > 300):
        return "high"
    return effort


def _number_of(full: Any, question: dict[str, Any]) -> int:
    for number, row in enumerate(full.version_questions(question["version_id"]), start=1):
        if row["id"] == question["id"]:
            return number
    return 1
