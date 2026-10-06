"""The Prompt Interpreter: turns everything the professor provided into an ExamSpec.

It reads the whole setup, not just the written prompt: settings, notes, course,
the uploaded material (overview plus the most relevant excerpts), the
previous-exam style profile and the professor's preferences. It returns an
enhanced prompt and a structured ExamSpec, which the professor reviews, edits
and approves before any exam is generated.

The professor's explicit settings are binding: after the model answers, they
are applied again in code, so the AI can never change them.
"""

from datetime import UTC, datetime
from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer, interpreter_instructions
from app.core.config import settings
from app.core.errors import ConflictError
from app.db.supabase import Database
from app.schemas.ai import DifficultySpec, ExamSpec, FormatSpec, InterpretationOutput
from app.services.assessment_context import (
    AssessmentContextService,
    AssessmentRecord,
    DocumentRecord,
    render_material_overview,
    render_preferences,
    render_professor_instructions,
    render_settings,
    render_style_profile,
)
from app.services.document_ingestion import DocumentIngestionService
from app.services.document_retrieval import DocumentRetrievalService, assign_refs, render_excerpts
from app.services.jobs import RunContext
from app.services.style_analysis import StyleAnalysisService

SPEC_ENVELOPE_VERSION = 1


def documents_signature(documents: list[DocumentRecord]) -> list[dict[str, Any]]:
    """The files an interpretation is based on (what changing them would invalidate)."""
    return sorted(
        (
            {"id": doc.id, "name": doc.original_name, "category": doc.category, "sha256": doc.content_sha256}
            for doc in documents
            if doc.processing_status == "ready"
        ),
        key=lambda item: item["id"],
    )


def stale_reasons(record: AssessmentRecord, documents: list[DocumentRecord]) -> list[str]:
    """Why the saved interpretation no longer matches the setup (empty when it does)."""
    inputs = (record.exam_spec or {}).get("inputs") or {}
    reasons: list[str] = []
    if record.spec_status == "stale":
        reasons.append("The assessment setup or its files changed after this interpretation.")
    if inputs.get("fingerprint") and inputs["fingerprint"] != record.setup.fingerprint():
        reasons.append("The assessment settings, notes or instructions changed.")
    used_ready = {item["id"] for item in inputs.get("documents", [])}
    used_all = used_ready | set(inputs.get("failed_document_ids", []))
    current_all = {doc.id for doc in documents}
    current_ready = {doc.id for doc in documents if doc.processing_status == "ready"}
    if used_all != current_all:
        reasons.append("Files were added or removed.")
    elif current_ready - used_ready:
        reasons.append("A file that couldn't be read before is now available.")
    return list(dict.fromkeys(reasons))


class PromptInterpreterService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()
        self.context = AssessmentContextService(db)

    async def interpret(self, assessment_id: str, run: RunContext | None = None) -> dict[str, Any]:
        record = await self.context.get_assessment(assessment_id)
        fingerprint = record.setup.fingerprint()

        if run:
            await run.stage("reading_material")
        documents = await self.context.list_documents(assessment_id)
        documents = await DocumentIngestionService(self.db, self.ai, self.usage).process_pending(documents)

        if run:
            await run.stage("analyzing_previous_exams")
        style = await StyleAnalysisService(self.db, self.ai, self.usage).ensure_profile(assessment_id, documents)
        preferences = await self.context.get_preferences()

        if run:
            await run.stage("interpreting")
        framer = DataFramer()
        excerpts = await self._excerpts(record, documents)
        attachments = [doc for doc in documents if doc.category == "additional_attachment" and doc.processing_status == "ready"]
        sections = [
            render_settings(record),
            render_professor_instructions(record),
            render_preferences(preferences),
            render_material_overview(documents, framer),
            render_style_profile(style, framer),
            render_excerpts(excerpts, framer),
            (
                "IMAGE ATTACHMENTS available for questions: "
                + ", ".join(f'"{doc.original_name}" (id {doc.id})' for doc in attachments)
                if attachments
                else "IMAGE ATTACHMENTS: none."
            ),
            "Now produce the enhanced prompt and the ExamSpec. Remember: the settings above are binding, "
            "and anything not provided stays unspecified unless generation genuinely needs an assumption.",
        ]
        course_docs = [doc for doc in documents if doc.category == "course_material" and doc.processing_status == "ready"]
        complex_request = len(course_docs) > 3 or len(record.setup.professor_prompt) > 800 or style is not None
        output = await self.ai.parse(
            purpose="interpret_assessment",
            instructions=interpreter_instructions(framer),
            input="\n\n".join(sections),
            schema=InterpretationOutput,
            effort="high" if complex_request else "medium",
            verbosity="medium",
            max_output_tokens=24_000,
            validate=lambda result: _interpretation_problems(result, record),
            usage=self.usage,
        )
        spec = apply_professor_settings(output.exam_spec, record)
        failed = [doc for doc in documents if doc.processing_status == "failed"]
        for doc in failed:
            warning = f"\"{doc.original_name}\" couldn't be read, so it wasn't used: {doc.processing_error or 'unknown problem'}"
            if warning not in spec.warnings:
                spec.warnings.append(warning)

        envelope = {
            "version": SPEC_ENVELOPE_VERSION,
            "spec": spec.model_dump(),
            "inputs": {
                "fingerprint": fingerprint,
                "settings": record.setup.to_row(),
                "course": record.course.label if record.course else None,
                "professor_prompt": record.setup.professor_prompt,
                "additional_notes": record.setup.additional_notes,
                "documents": documents_signature(documents),
                "failed_documents": [doc.original_name for doc in failed],
                "failed_document_ids": [doc.id for doc in failed],
                "style_profile_used": style is not None,
                "preferences_used": bool(
                    preferences.explicit_notes or (preferences.learning_enabled and preferences.learned.get("summary"))
                ),
                "excerpts": [{"ref": excerpt.ref, "label": excerpt.label} for excerpt in excerpts],
            },
            "model": settings.reasoning_model,
            "generated_at": datetime.now(UTC).isoformat(),
        }

        # If the professor changed the setup while this ran, the result is
        # already outdated: save it, but as stale.
        latest = await self.context.get_assessment(assessment_id)
        latest_documents = await self.context.list_documents(assessment_id)
        changed = latest.setup.fingerprint() != fingerprint or {doc.id for doc in latest_documents} != {
            doc.id for doc in documents
        }
        await self.db.update(
            "exam_projects",
            {
                "enhanced_prompt": output.enhanced_prompt.strip(),
                "exam_spec": envelope,
                "spec_status": "stale" if changed else "draft",
                "spec_generated_at": envelope["generated_at"],
                "spec_approved_at": None,
            },
            filters=[("id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )
        return envelope

    async def _excerpts(self, record: AssessmentRecord, documents: list[DocumentRecord]):
        ready = [doc for doc in documents if doc.processing_status == "ready" and doc.category == "course_material"]
        if not ready:
            return []
        names = {doc.id: doc.original_name for doc in documents}
        retrieval = DocumentRetrievalService(self.db, self.ai, self.usage)
        queries = [
            record.setup.professor_prompt,
            record.setup.additional_notes,
            " ".join(part for part in [record.course.label if record.course else "", record.title or ""] if part),
        ]
        excerpts = await retrieval.search(
            record.id, queries, document_names=names, per_query=8, max_excerpts=10, max_chars=20_000
        )
        if len(excerpts) < 8:
            # A thin or empty prompt: add a spread of the material for broad coverage.
            spread = await retrieval.spread([doc.id for doc in ready], document_names=names, per_document=2, max_chars=12_000)
            seen = {excerpt.chunk_id for excerpt in excerpts}
            excerpts.extend(excerpt for excerpt in spread if excerpt.chunk_id not in seen)
        return assign_refs(excerpts[:16])

    # -- Review actions ------------------------------------------------------------

    async def update_enhanced_prompt(self, assessment_id: str, enhanced_prompt: str) -> None:
        record = await self.context.get_assessment(assessment_id)
        if record.spec_status == "none" or not record.exam_spec:
            raise ConflictError("Improve with AI first, then edit the result.", code="spec_missing")
        await self.db.update(
            "exam_projects",
            {
                "enhanced_prompt": enhanced_prompt.strip(),
                # An edited prompt needs approving again (a stale one stays stale).
                "spec_status": "stale" if record.spec_status == "stale" else "draft",
                "spec_approved_at": None,
            },
            filters=[("id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )

    async def approve(self, assessment_id: str) -> None:
        record = await self.context.get_assessment(assessment_id)
        if record.spec_status == "none" or not record.exam_spec:
            raise ConflictError("There's no interpretation to approve yet.", code="spec_missing")
        documents = await self.context.list_documents(assessment_id)
        reasons = stale_reasons(record, documents)
        if reasons:
            await self._mark_stale(assessment_id)
            raise ConflictError(
                "Your setup changed since this interpretation, so it can't be approved. Regenerate it first.",
                code="spec_stale",
            )
        await self.db.update(
            "exam_projects",
            {"spec_status": "approved", "spec_approved_at": datetime.now(UTC).isoformat()},
            filters=[("id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )

    async def require_approved(self, assessment_id: str) -> tuple[AssessmentRecord, ExamSpec]:
        """The approved, up-to-date spec, or a clear error. Generation always calls this."""
        record = await self.context.get_assessment(assessment_id)
        if record.spec_status != "approved" or not record.exam_spec:
            raise ConflictError("Approve the assessment plan before generating the exam.", code="spec_not_approved")
        documents = await self.context.list_documents(assessment_id)
        if stale_reasons(record, documents):
            await self._mark_stale(assessment_id)
            raise ConflictError(
                "Your setup changed after the plan was approved. Review and approve the updated plan first.",
                code="spec_stale",
            )
        return record, ExamSpec.model_validate(record.exam_spec["spec"])

    async def _mark_stale(self, assessment_id: str) -> None:
        await self.db.update(
            "exam_projects",
            {"spec_status": "stale"},
            filters=[("id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )


def _interpretation_problems(output: InterpretationOutput, record: AssessmentRecord) -> list[str]:
    problems: list[str] = []
    spec = output.exam_spec
    if len(output.enhanced_prompt.strip()) < 60:
        problems.append("enhanced_prompt is too short to guide an exam author; write a complete brief.")
    if spec.question_format and spec.question_format.mcq_percent + spec.question_format.subjective_percent != 100:
        problems.append("question_format percentages must add up to 100.")
    if spec.difficulty and spec.difficulty.easy_percent + spec.difficulty.medium_percent + spec.difficulty.hard_percent != 100:
        problems.append("difficulty percentages must add up to 100.")
    if (
        spec.question_count_min is not None
        and spec.question_count_max is not None
        and spec.question_count_min > spec.question_count_max
    ):
        problems.append("question_count_min can't be larger than question_count_max.")
    if spec.versions != record.setup.number_of_versions:
        problems.append(f"versions must be {record.setup.number_of_versions}, as the professor set.")
    return problems


def apply_professor_settings(spec: ExamSpec, record: AssessmentRecord) -> ExamSpec:
    """Re-applies every explicit setting, so the AI can't override the professor."""
    setup = record.setup
    spec = spec.model_copy(deep=True)
    spec.versions = setup.number_of_versions
    if record.title:
        spec.assessment_title = record.title
    if record.course:
        spec.course = record.course.label
    if setup.duration_minutes:
        spec.duration_minutes = setup.duration_minutes
        spec.duration_source = "professor"
    elif spec.duration_minutes is None:
        spec.duration_source = None
    # Values the professor stated only in their written instructions keep the
    # model's provenance; values from the form are always the form's.
    if setup.mcq_percentage is not None and setup.subjective_percentage is not None:
        spec.question_format = FormatSpec(
            mcq_percent=setup.mcq_percentage, subjective_percent=setup.subjective_percentage, source="professor"
        )
    if setup.easy_percentage is not None and setup.medium_percentage is not None and setup.hard_percentage is not None:
        spec.difficulty = DifficultySpec(
            easy_percent=setup.easy_percentage,
            medium_percent=setup.medium_percentage,
            hard_percent=setup.hard_percentage,
            source="professor",
        )
    if (
        spec.question_count_min is not None
        and spec.question_count_max is not None
        and spec.question_count_min > spec.question_count_max
    ):
        spec.question_count_min, spec.question_count_max = spec.question_count_max, spec.question_count_min
    return spec
