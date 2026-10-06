"""Exam editing: questions, AI revisions, the quality check, final review, export, and Build with AI."""

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.ai.openai_service import OpenAIService
from app.api.deps import get_ai, get_db
from app.core.errors import ConflictError, InvalidInputError
from app.db.supabase import Database
from app.schemas.api import (
    ApprovalIn,
    BuilderChatOut,
    BuilderMessageIn,
    ExamOut,
    FinalizeOut,
    MessageOut,
    QuestionResultOut,
    ReadinessOut,
    ReorderIn,
    ReviseQuestionIn,
    RevisionOut,
    RunOut,
    StartedRunOut,
    ToolCallIn,
    TranscriptIn,
    VariantsOut,
)
from app.schemas.common import parse_id
from app.services.assessment_context import AssessmentContextService
from app.services.document_ingestion import BUCKET
from app.services.exam_builder import ExamBuilderService
from app.services.exam_review import ExamReviewService
from app.services.exam_store import ExamStore, serialize_exam, serialize_question
from app.services.jobs import RunContext, job_runner, public_run
from app.services.pdf_export import ExportHeader, PdfExportService
from app.services.personalization import PreferenceService
from app.services.question_revision import QuestionEdit, QuestionRevisionService
from app.services.readiness import readiness

router = APIRouter(prefix="/api", tags=["exams"])


async def _question_out(db: Database, row: dict[str, Any]) -> dict[str, Any]:
    """A question with its number in its version (numbering follows the order)."""
    positions = await db.select(
        "exam_questions",
        columns="id, position",
        filters=[("version_id", "eq", row["version_id"]), ("professor_id", "eq", db.professor_id)],
        order=[("position", "asc")],
    )
    number = next((index for index, item in enumerate(positions, start=1) if item["id"] == row["id"]), 0)
    revisions = await db.select(
        "question_revisions",
        columns="id",
        filters=[("question_id", "eq", row["id"]), ("professor_id", "eq", db.professor_id)],
    )
    return serialize_question(row, number, len(revisions))


async def _result(db: Database, result: dict[str, Any]) -> QuestionResultOut:
    return QuestionResultOut.model_validate(
        {
            "question": await _question_out(db, result["question"]),
            "changeSummary": result.get("changeSummary"),
            "notes": result.get("notes"),
            "distribution": result.get("distribution"),
            "distributionWarnings": result.get("distributionWarnings", []),
        }
    )


# -----------------------------------------------------------------------------
# Exam
# -----------------------------------------------------------------------------


@router.get("/exams/{exam_id}")
async def get_exam(exam_id: str, db: Database = Depends(get_db)) -> ExamOut:
    return ExamOut.model_validate(serialize_exam(await ExamStore(db).load(exam_id)))


@router.post("/exams/{exam_id}/review", status_code=status.HTTP_202_ACCEPTED)
async def review_exam(exam_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)) -> StartedRunOut:
    """Runs the AI quality check again (for example after many edits)."""
    exam = await ExamStore(db).get_exam(exam_id)
    if exam["status"] == "generating":
        raise ConflictError("Wait until the exam has finished generating.", code="exam_generating")

    async def work(run: RunContext) -> None:
        await run.stage("reviewing")
        await ExamReviewService(db, ai, run.usage).review(exam["id"])

    run = await job_runner.start(db, assessment_id=exam["exam_project_id"], kind="review", work=work, exam_id=exam["id"])
    return StartedRunOut(run=RunOut.model_validate(public_run(run)))


@router.get("/exams/{exam_id}/readiness")
async def exam_readiness(exam_id: str, db: Database = Depends(get_db)) -> ReadinessOut:
    return ReadinessOut.model_validate(readiness(await ExamStore(db).load(exam_id)))


@router.post("/exams/{exam_id}/finalization")
async def finalize_exam(exam_id: str, db: Database = Depends(get_db)) -> FinalizeOut:
    """The professor marks the exam as final. Personalization learns from finalized exams only."""
    store = ExamStore(db)
    full = await store.load(exam_id)
    report = readiness(full)
    if not report["canExport"]:
        raise ConflictError("The exam isn't complete yet.", code="exam_incomplete")
    finalized_at = datetime.now(UTC).isoformat()
    await store.update_exam(full.exam["id"], {"finalized_at": finalized_at})
    primary = full.primary_version
    await PreferenceService(db).record_exam_finalized(
        full.exam["exam_project_id"],
        full.version_questions(primary["id"]),
        full.spec,
        len(full.versions),
        [section["title"] for section in sorted(full.sections, key=lambda s: s["position"])],
    )
    return FinalizeOut(finalized_at=finalized_at)


@router.get("/exams/{exam_id}/export")
async def export_exam(
    exam_id: str,
    kind: Literal["student", "answer_key", "both"] = Query("student"),
    version: str = Query("all"),
    confirm: bool = Query(False),
    db: Database = Depends(get_db),
) -> Response:
    """PDFs for printing: the student paper and/or the answer key, for one or all versions."""
    store = ExamStore(db)
    full = await store.load(exam_id)
    report = readiness(full)
    if not report["canExport"]:
        raise ConflictError("The exam can't be exported until it is complete.", code="export_blocked")
    if report["requiresConfirmation"] and not confirm:
        serious = "; ".join(check["label"] for check in report["checks"] if check["status"] == "serious")
        raise ConflictError(f"Please confirm the export: {serious}", code="export_needs_confirmation")

    labels = [v["label"] for v in full.versions]
    versions = labels if version == "all" else [version]
    if any(label not in labels for label in versions):
        raise InvalidInputError("Choose one of this exam's versions.")
    kinds = ["student", "answer_key"] if kind == "both" else [kind]

    record = await AssessmentContextService(db).get_assessment(full.exam["exam_project_id"])
    spec = full.spec
    title = record.title or (spec.assessment_title if spec else None) or (spec.assessment_type if spec else None) or "Examination"
    header = ExportHeader(
        course=record.course.label if record.course else None,
        title=title,
        duration_minutes=spec.duration_minutes if spec else record.setup.duration_minutes,
    )

    figures: dict[str, bytes] = {}
    figure_ids = {q["figure_document_id"] for q in full.questions if q.get("figure_document_id")}
    for figure_id in figure_ids:
        document = await db.select_one(
            "documents",
            columns="id, storage_path, mime_type",
            filters=[("id", "eq", figure_id), ("professor_id", "eq", db.professor_id)],
        )
        if document and (document.get("mime_type") or "").startswith("image/"):
            figures[figure_id] = await db.download(BUCKET, document["storage_path"])

    content, media_type, filename = PdfExportService().build_many(full, header, versions=versions, kinds=kinds, figures=figures)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.put("/exams/{exam_id}/versions/{version_id}/order")
async def reorder_questions(
    exam_id: str, version_id: str, body: ReorderIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> ExamOut:
    await QuestionRevisionService(db, ai).reorder(exam_id, version_id, body.question_ids)
    return ExamOut.model_validate(serialize_exam(await ExamStore(db).load(exam_id)))


# -----------------------------------------------------------------------------
# Questions
# -----------------------------------------------------------------------------


@router.patch("/questions/{question_id}")
async def edit_question(
    question_id: str, body: QuestionEdit, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> QuestionResultOut:
    return await _result(db, await QuestionRevisionService(db, ai).edit(question_id, body))


@router.post("/questions/{question_id}/revision")
async def revise_question(
    question_id: str, body: ReviseQuestionIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> QuestionResultOut:
    """Asks the AI to change one question; the answer key changes with it."""
    result = await QuestionRevisionService(db, ai).revise(question_id, instruction=body.instruction, preset=body.preset)
    return await _result(db, result)


@router.post("/questions/{question_id}/solution")
async def regenerate_solution(
    question_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> QuestionResultOut:
    return await _result(db, await QuestionRevisionService(db, ai).regenerate_solution(question_id))


@router.put("/questions/{question_id}/approval")
async def set_approval(
    question_id: str, body: ApprovalIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> QuestionResultOut:
    return await _result(db, await QuestionRevisionService(db, ai).set_approved(question_id, body.approved))


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)) -> Response:
    await QuestionRevisionService(db, ai).delete(question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/questions/{question_id}/revisions")
async def list_revisions(question_id: str, db: Database = Depends(get_db)) -> list[RevisionOut]:
    store = ExamStore(db)
    question = await store.get_question(question_id)
    return [RevisionOut.model_validate(revision) for revision in await store.revisions(question["id"])]


@router.post("/questions/{question_id}/revisions/{revision_id}/restore")
async def restore_revision(
    question_id: str, revision_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> QuestionResultOut:
    return await _result(db, await QuestionRevisionService(db, ai).restore(question_id, revision_id))


@router.post("/questions/{question_id}/variants")
async def sync_variants(question_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)) -> VariantsOut:
    """Rewrites this question's equivalents in the other versions to match it."""
    result = await QuestionRevisionService(db, ai).sync_variants(question_id)
    questions = [await _question_out(db, row) for row in result["questions"]]
    return VariantsOut.model_validate({"questions": questions, "skippedVersions": result["skippedVersions"]})


# -----------------------------------------------------------------------------
# Build with AI
# -----------------------------------------------------------------------------


@router.get("/exams/{exam_id}/builder/messages")
async def builder_messages(exam_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)) -> list[MessageOut]:
    exam_id = parse_id(exam_id, what="exam")
    return [MessageOut.model_validate(message) for message in await ExamBuilderService(db, ai).messages(exam_id)]


@router.post("/exams/{exam_id}/builder/messages")
async def builder_chat(
    exam_id: str, body: BuilderMessageIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> BuilderChatOut:
    """One text-chat turn with ProfPilot while building the exam."""
    exam_id = parse_id(exam_id, what="exam")
    return BuilderChatOut.model_validate(await ExamBuilderService(db, ai).chat(exam_id, body.content))


@router.post("/exams/{exam_id}/builder/tools/{tool_name}")
async def builder_tool(
    exam_id: str, tool_name: str, body: ToolCallIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> dict[str, Any]:
    """Runs a tool the voice assistant chose. Same checks as every other endpoint."""
    exam_id = parse_id(exam_id, what="exam")
    return await ExamBuilderService(db, ai).execute(exam_id, tool_name, body.arguments)


@router.post("/exams/{exam_id}/builder/transcript")
async def builder_transcript(
    exam_id: str, body: TranscriptIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> MessageOut:
    """Keeps what was said in a voice call in the conversation history."""
    exam_id = parse_id(exam_id, what="exam")
    builder = ExamBuilderService(db, ai)
    await builder.store.get_exam(exam_id)
    message = await builder.add_message(exam_id, body.role, body.content, channel="voice")
    return MessageOut.model_validate({**message, "createdAt": message["created_at"]})
