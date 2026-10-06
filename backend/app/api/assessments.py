"""Assessment-level AI endpoints: status, document processing, interpretation, exam creation.

Every route needs a signed-in professor (Bearer token) and works through their
own database connection, so Row Level Security applies on top of the checks
here. Long work starts a background job and returns it immediately (202).
"""

from typing import Any

from fastapi import APIRouter, Depends, status

from app.ai.openai_service import OpenAIService
from app.api.deps import get_ai, get_db
from app.core.errors import ConflictError, NotFoundError
from app.db.supabase import Database
from app.schemas.api import (
    AiStatusOut,
    CreateExamIn,
    CreateExamOut,
    ExamOut,
    InterpretationEditIn,
    InterpretationOut,
    ProcessDocumentsIn,
    RunOut,
    StartedRunOut,
)
from app.schemas.common import parse_id
from app.services.assessment_context import AssessmentContextService
from app.services.document_ingestion import DocumentIngestionService
from app.services.exam_generation import ExamGenerationService
from app.services.exam_store import ExamStore, serialize_exam
from app.services.jobs import RUN_COLUMNS, RunContext, is_stale, job_runner, public_run
from app.services.prompt_interpreter import PromptInterpreterService, stale_reasons
from app.services.style_analysis import StyleAnalysisService

router = APIRouter(prefix="/api", tags=["assessments"])


@router.get("/assessments/{assessment_id}/ai-status")
async def ai_status(assessment_id: str, db: Database = Depends(get_db)) -> AiStatusOut:
    context = AssessmentContextService(db)
    record = await context.get_assessment(assessment_id)
    documents = await context.list_documents(record.id)
    reasons = stale_reasons(record, documents) if record.spec_status != "none" else []
    style = await context.get_style_profile(record.id)
    exam = await ExamStore(db).exam_for_assessment(record.id)
    exam_status = None
    if exam:
        count = await db.select(
            "exam_questions",
            columns="id",
            filters=[("exam_id", "eq", exam["id"]), ("professor_id", "eq", db.professor_id)],
        )
        exam_status = {
            "id": exam["id"],
            "mode": exam["mode"],
            "status": exam["status"],
            "reviewStatus": exam["review_status"],
            "questionCount": len(count),
            "finalizedAt": exam.get("finalized_at"),
        }
    runs = await db.select(
        "ai_runs",
        columns=RUN_COLUMNS,
        filters=[("exam_project_id", "eq", record.id), ("professor_id", "eq", db.professor_id)],
        order=[("started_at", "desc")],
        limit=10,
    )
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        latest.setdefault(run["kind"], run)
    return AiStatusOut.model_validate(
        {
            "assessmentId": record.id,
            "spec": {
                "status": "stale" if reasons else record.spec_status,
                "generatedAt": record.spec_generated_at,
                "approvedAt": record.spec_approved_at,
                "staleReasons": reasons,
            },
            "documents": [
                {
                    "id": doc.id,
                    "name": doc.original_name,
                    "category": doc.category,
                    "status": doc.processing_status,
                    "error": doc.processing_error,
                    "pageCount": doc.page_count,
                    "title": (doc.summary or {}).get("title"),
                }
                for doc in documents
            ],
            "styleProfile": ({"examsAnalyzed": style[0].exams_analyzed, "summary": style[0].summary} if style else None),
            "exam": exam_status,
            "runs": [public_run(run) for run in latest.values()],
        }
    )


@router.post("/assessments/{assessment_id}/documents/processing", status_code=status.HTTP_202_ACCEPTED)
async def process_documents(
    assessment_id: str, body: ProcessDocumentsIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> StartedRunOut:
    """Reads, embeds and summarises files that aren't ready (or one file to retry)."""
    context = AssessmentContextService(db)
    record = await context.get_assessment(assessment_id)
    target = parse_id(body.document_id, what="file") if body.document_id else None
    if target:
        document = await context.get_document(target)
        if document.exam_project_id != record.id:
            raise NotFoundError("This file doesn't belong to this assessment.")

    async def work(run: RunContext) -> None:
        await run.stage("reading_files")
        documents = await context.list_documents(record.id)
        if target:
            documents = [doc for doc in documents if doc.id == target]
            for doc in documents:
                await DocumentIngestionService(db, ai, run.usage).process(doc)
        else:
            await DocumentIngestionService(db, ai, run.usage).process_pending(documents, retry_failed=body.retry_failed)
        await run.stage("analyzing_previous_exams")
        await StyleAnalysisService(db, ai, run.usage).ensure_profile(record.id, await context.list_documents(record.id))

    run = await job_runner.start(db, assessment_id=record.id, kind="document_processing", work=work)
    return StartedRunOut(run=RunOut.model_validate(public_run(run)))


@router.post("/assessments/{assessment_id}/interpretation", status_code=status.HTTP_202_ACCEPTED)
async def start_interpretation(
    assessment_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> StartedRunOut:
    """'Improve with AI': analyses everything provided and drafts the ExamSpec."""
    record = await AssessmentContextService(db).get_assessment(assessment_id)

    async def work(run: RunContext) -> None:
        await PromptInterpreterService(db, ai, run.usage).interpret(record.id, run)

    run = await job_runner.start(db, assessment_id=record.id, kind="interpretation", work=work)
    return StartedRunOut(run=RunOut.model_validate(public_run(run)))


async def _interpretation(db: Database, assessment_id: str) -> InterpretationOut:
    context = AssessmentContextService(db)
    record = await context.get_assessment(assessment_id)
    envelope = record.exam_spec or {}
    reasons: list[str] = []
    if record.spec_status != "none":
        reasons = stale_reasons(record, await context.list_documents(record.id))
    return InterpretationOut.model_validate(
        {
            "status": "stale" if reasons else record.spec_status,
            "staleReasons": reasons,
            "enhancedPrompt": record.enhanced_prompt,
            "spec": envelope.get("spec"),
            "inputs": envelope.get("inputs"),
            "generatedAt": record.spec_generated_at,
            "approvedAt": record.spec_approved_at,
            "model": envelope.get("model"),
        }
    )


@router.get("/assessments/{assessment_id}/interpretation")
async def get_interpretation(assessment_id: str, db: Database = Depends(get_db)) -> InterpretationOut:
    return await _interpretation(db, assessment_id)


@router.patch("/assessments/{assessment_id}/interpretation")
async def edit_interpretation(
    assessment_id: str, body: InterpretationEditIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> InterpretationOut:
    """The professor edits the enhanced prompt; it then needs approving again."""
    await PromptInterpreterService(db, ai).update_enhanced_prompt(assessment_id, body.enhanced_prompt)
    return await _interpretation(db, assessment_id)


@router.post("/assessments/{assessment_id}/interpretation/approval")
async def approve_interpretation(
    assessment_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> InterpretationOut:
    await PromptInterpreterService(db, ai).approve(assessment_id)
    return await _interpretation(db, assessment_id)


@router.post("/assessments/{assessment_id}/exam", status_code=status.HTTP_202_ACCEPTED)
async def create_exam(
    assessment_id: str, body: CreateExamIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> CreateExamOut:
    """Generates the full exam (a job), or starts Build with AI (an empty exam)."""
    store = ExamStore(db)
    interpreter = PromptInterpreterService(db, ai)
    record, spec = await interpreter.require_approved(assessment_id)
    existing = await store.exam_for_assessment(record.id)

    if body.resume:
        if not existing or existing["mode"] != "full" or existing["status"] != "failed":
            raise ConflictError("There's no stopped generation to continue.", code="nothing_to_resume")
        exam = existing
        await store.update_exam(exam["id"], {"status": "generating", "error_message": None})
    else:
        if existing:
            if not body.replace:
                raise ConflictError("This assessment already has an exam. Replace it to start again.", code="exam_exists")
            if existing["status"] == "generating":
                running = await db.select(
                    "ai_runs",
                    columns=RUN_COLUMNS,
                    filters=[("exam_id", "eq", existing["id"]), ("status", "eq", "running")],
                )
                if any(not is_stale(run) for run in running):
                    raise ConflictError("This exam is still being generated.", code="job_running")
            await store.delete_exam(existing["id"])
        exam = await store.create_exam(
            record.id,
            mode=body.mode,
            spec_envelope=record.exam_spec or {},
            enhanced_prompt=record.enhanced_prompt,
            status="generating" if body.mode == "full" else "building",
        )
        await db.update(
            "exam_projects",
            {"generation_mode": body.mode},
            filters=[("id", "eq", record.id), ("professor_id", "eq", db.professor_id)],
            columns="id",
        )

    if body.mode == "interactive" and not body.resume:
        await store.create_versions(exam["id"], spec.versions)
        await store.create_sections(exam["id"], [(section.title, section.notes) for section in spec.sections])
        return CreateExamOut(exam=ExamOut.model_validate(serialize_exam(await store.load(exam["id"]))))

    async def work(run: RunContext) -> None:
        await ExamGenerationService(db, ai, run.usage).run_full(record.id, exam["id"], run)

    try:
        run = await job_runner.start(db, assessment_id=record.id, kind="generation", work=work, exam_id=exam["id"])
    except ConflictError:
        await store.update_exam(exam["id"], {"status": "failed", "error_message": "Generation is already running."})
        raise
    return CreateExamOut(run=RunOut.model_validate(public_run(run)))


@router.get("/assessments/{assessment_id}/exam")
async def get_exam(assessment_id: str, db: Database = Depends(get_db)) -> ExamOut:
    record = await AssessmentContextService(db).get_assessment(assessment_id)
    store = ExamStore(db)
    exam = await store.exam_for_assessment(record.id)
    if exam is None:
        raise NotFoundError("This assessment has no exam yet.")
    return ExamOut.model_validate(serialize_exam(await store.load(exam["id"])))


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: Database = Depends(get_db)) -> RunOut:
    run_id = parse_id(run_id, what="job")
    run = await db.select_one(
        "ai_runs", columns=RUN_COLUMNS, filters=[("id", "eq", run_id), ("professor_id", "eq", db.professor_id)]
    )
    if run is None:
        raise NotFoundError("This job doesn't exist.")
    return RunOut.model_validate(public_run(run))
