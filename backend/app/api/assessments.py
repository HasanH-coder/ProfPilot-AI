"""Assessment-level AI endpoints: status, document processing, interpretation, exam creation.

Every route needs a signed-in professor (Bearer token) and works through their
own database connection, so Row Level Security applies on top of the checks
here. Long work starts a background job and returns it immediately (202).
"""

import asyncio
import contextlib
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.ai.openai_service import OpenAIService, Usage
from app.api.deps import get_ai, get_db
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.timing import timed
from app.db.supabase import Database
from app.schemas.api import (
    AiStatusOut,
    CreateExamIn,
    CreateExamOut,
    DocumentProcessingOut,
    DocumentStatusOut,
    ExamOut,
    InterpretationEditIn,
    InterpretationOut,
    ProcessDocumentsIn,
    RunOut,
    StartedRunOut,
)
from app.schemas.common import parse_id
from app.services.assessment_context import DOCUMENT_COLUMNS, AssessmentContextService, DocumentRecord
from app.services.document_ingestion import DocumentIngestionService
from app.services.document_parsers import PDF, PPTX
from app.services.exam_generation import ExamGenerationService
from app.services.exam_store import ExamStore, serialize_exam
from app.services.jobs import RUN_COLUMNS, RunContext, is_stale, job_runner, public_run
from app.services.lecture_knowledge import (
    LectureKnowledgeService,
    extracting,
    extraction_state,
    finish_extracting,
    is_building,
    is_extracting,
    picture_overview,
    start_extracting,
)
from app.services.lecture_knowledge import overview as lecture_overview
from app.services.prompt_interpreter import PromptInterpreterService, stale_reasons
from app.services.slides import reading_problems
from app.services.source_preparation import settled
from app.services.style_analysis import StyleAnalysisService

logger = logging.getLogger("profpilot.api")

router = APIRouter(prefix="/api", tags=["assessments"])

# Sources this server is reading right now. One that says "processing" but isn't
# here was interrupted (for example by a restart) and may be started again.
_reading: set[str] = set()
# Lectures whose pictures this server is reading right now, and why the last attempt
# failed (if it did), for the Sources page.
_reading_pictures: set[str] = set()
_picture_errors: dict[str, str] = {}
# Course sources this server is preparing ("queued" or "running"), and those it has prepared
# since it started: one whose preparation failed isn't tried again on its own until a restart
# (Retry does it sooner), so a file that always fails never keeps spending model calls.
_preparing: dict[str, str] = {}
_prepared: set[str] = set()
# Two files at a time: their knowledge is built with several model calls each already.
_PREPARE_AT_ONCE = 2
# A preparation run starts no new file after this long, so it never outlives the session it
# runs with; the rest wait for the next visit to Sources.
_PREPARE_SECONDS = 40 * 60


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


@router.get("/documents/{document_id}/status")
async def document_status(document_id: str, db: Database = Depends(get_db)) -> DocumentStatusOut:
    """Returns processing state for one owned document, including a reusable course source."""
    document = await AssessmentContextService(db).get_document(document_id)
    return DocumentStatusOut.model_validate(
        {
            "id": document.id,
            "name": document.original_name,
            "category": document.category,
            "status": document.processing_status,
            "error": document.processing_error,
            "pageCount": document.page_count,
            "title": (document.summary or {}).get("title"),
        }
    )


@router.post("/documents/{document_id}/processing", status_code=status.HTTP_202_ACCEPTED)
async def process_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
    ai: OpenAIService = Depends(get_ai),
) -> DocumentProcessingOut:
    """Starts (or resumes) ingestion for one owned source without requiring an assessment."""
    document = await AssessmentContextService(db).get_document(document_id)
    if document.id in _reading:
        return DocumentProcessingOut(id=document.id, status="processing")
    # Reserved before the first await, so a second request can't start it too.
    _reading.add(document.id)
    try:
        claimed = await db.update(
            "documents",
            {"processing_status": "processing", "processing_error": None},
            filters=[
                ("id", "eq", document.id),
                ("professor_id", "eq", db.professor_id),
                ("processing_status", "in", ["pending", "processing", "failed"]),
            ],
            columns="id",
        )
    except BaseException:
        _reading.discard(document.id)
        raise
    if not claimed:
        _reading.discard(document.id)
        return DocumentProcessingOut(id=document.id, status=document.processing_status)

    async def read() -> None:
        usage = Usage()
        try:
            # Logs how long reading took and the model calls it made (never the content).
            async with timed("source_ingestion", usage):
                await DocumentIngestionService(db, ai, usage).process(document)
        finally:
            _reading.discard(document.id)

    background_tasks.add_task(read)
    return DocumentProcessingOut(id=document.id, status="processing")


@router.get("/documents/{document_id}/knowledge")
async def document_knowledge(
    document_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> dict[str, Any]:
    """An owned document's lecture knowledge: state, structure, topics, coverage, warnings and a build estimate."""
    document = await AssessmentContextService(db).get_document(document_id)
    service = LectureKnowledgeService(db, ai)
    knowledge = await service.get(document.id)
    result = lecture_overview(knowledge, document)
    interrupted = "Building the lecture knowledge was interrupted. Build it again."
    if result["state"] == "building" and not is_building(document.id):
        result["state"] = "failed"  # interrupted (e.g. by a restart): it can be rebuilt
        result["error"] = interrupted
    if result["rebuild"] and result["rebuild"]["state"] == "building" and not is_building(document.id):
        result["rebuild"] = {"state": "failed", "error": interrupted}
    # The stored text, unless it's being replaced right now.
    chunks = [] if is_extracting(document.id) else await service.chunks(document.id)
    # Whether the stored text has page markers (a free re-read adds them; see refresh_extraction).
    result["extraction"] = {
        "state": "processing" if is_extracting(document.id) else extraction_state(chunks),
        "rereadable": document.processing_status == "ready" and (document.mime_type or "").lower() in _REREADABLE,
    }
    # Which pages' pictures (diagrams, charts, tables, code) ProfPilot has read, and which not yet.
    preparing = _preparing.get(document.id) == "running" or document.id in _reading
    reading = document.id in _reading_pictures or (preparing and is_extracting(document.id))
    result["pictures"] = {
        **picture_overview(chunks, knowledge),
        "state": "reading" if reading else "idle",
        "readable": _pictures_readable(document),
        "error": _picture_errors.get(document.id),
    }
    # Being brought up to date by the Sources page (see prepare_sources).
    result["preparation"] = _preparing.get(document.id)
    if result["state"] in ("none", "outdated") and document.id in _reading:
        result["state"] = "building"  # read just now: its knowledge is built right after
    return result


# Files whose text a re-read can improve (page and slide markers); never images (AI-read).
_REREADABLE = {PDF, PPTX}


def _pictures_readable(document: DocumentRecord) -> bool:
    """A PowerPoint lecture, once read: its pictures are read into its text (a PDF's slides are shown as
    images when the exam is written instead; see slides.py)."""
    return (
        document.category == "course_material"
        and document.processing_status == "ready"
        and (document.mime_type or "").lower() == PPTX
    )


@router.post("/documents/{document_id}/figures", status_code=status.HTTP_202_ACCEPTED)
async def read_document_pictures(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
    ai: OpenAIService = Depends(get_ai),
) -> dict[str, Any]:
    """Reads a PowerPoint lecture's pictures again (diagrams, charts, tables, code) when the professor
    retries a reading that failed. Pictures already read are kept, never paid for twice. The lecture
    knowledge is rebuilt from them by the next preparation (see prepare_sources); the profile stays in
    use meanwhile."""
    document = await AssessmentContextService(db).get_document(document_id)
    if not _pictures_readable(document):
        raise ConflictError("Pictures are read in PowerPoint course material, once it's read.", code="not_readable")
    if document.id in _reading or is_building(document.id) or is_extracting(document.id):
        raise ConflictError("This file is busy (being read or analysed). Try again when it's done.", code="busy")
    # Reserved before the first await, so a second request can't start it too.
    start_extracting(document.id)
    _reading_pictures.add(document.id)
    _picture_errors.pop(document.id, None)

    async def read() -> None:
        usage = Usage()
        try:
            async with timed("source_pictures", usage):
                await DocumentIngestionService(db, ai, usage).refresh_extraction(document, read_pictures=True)
        except AppError as error:
            _picture_errors[document.id] = error.message
        except Exception as error:
            logger.warning("Reading the pictures of %s failed: %s", document.id, type(error).__name__)
            _picture_errors[document.id] = "The pictures couldn't be read. Try again."
        finally:
            finish_extracting(document.id)
            _reading_pictures.discard(document.id)

    background_tasks.add_task(read)
    return {"state": "reading"}


@router.post("/sources/preparation", status_code=status.HTTP_202_ACCEPTED)
async def prepare_sources(
    background_tasks: BackgroundTasks, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> dict[str, Any]:
    """Brings the professor's course sources up to date for AI work, without them asking.

    Started by the Sources page whenever it opens. Each file whose text predates the current
    reading (page markers; a PowerPoint file's pictures) or whose lecture knowledge is missing,
    outdated or older than its pictures is prepared in the background: a lecture PDF's slides' words
    are kept (a PowerPoint file's pictures read), then its knowledge is built. A prepared file needs
    no closer look, so this costs nothing once all are done. Returns the files being prepared, for
    their progress to be shown.
    """
    rows = await db.select(
        "documents",
        columns=f"{DOCUMENT_COLUMNS}, knowledge",
        filters=[
            ("professor_id", "eq", db.professor_id),
            ("exam_project_id", "is", None),
            ("category", "eq", "course_material"),
            ("processing_status", "eq", "ready"),
            ("deletion_pending", "eq", False),
        ],
        order=[("created_at", "asc")],
    )
    queue: list[DocumentRecord] = []
    for row in rows:
        document = DocumentRecord.from_row(row)
        busy = document.id in _reading or is_building(document.id) or is_extracting(document.id)
        if not document.course_id or document.id in _preparing or document.id in _prepared or busy:
            continue
        if not settled(document, row.get("knowledge")):
            _preparing[document.id] = "queued"
            queue.append(document)
    if queue:
        background_tasks.add_task(_prepare_all, db, ai, queue)
    return {"preparing": [row["id"] for row in rows if row["id"] in _preparing]}


async def _prepare_all(db: Database, ai: OpenAIService, queue: list[DocumentRecord]) -> None:
    """Prepares the queued sources, one at a time."""
    deadline = time.monotonic() + _PREPARE_SECONDS

    async def worker() -> None:
        while queue:
            document = queue.pop(0)
            busy = document.id in _reading or is_building(document.id) or is_extracting(document.id)
            if busy or time.monotonic() > deadline:
                _preparing.pop(document.id, None)  # left for the next visit
                continue
            _preparing[document.id] = "running"
            usage = Usage()
            try:
                async with timed("source_preparation", usage):
                    await DocumentIngestionService(db, ai, usage).prepare(document)
            except Exception as error:  # one file's problem never stops the others
                logger.warning("Preparing source %s failed: %s", document.id, type(error).__name__)
            finally:
                _prepared.add(document.id)
                _preparing.pop(document.id, None)

    await asyncio.gather(*(worker() for _ in range(_PREPARE_AT_ONCE)))


@router.post("/documents/{document_id}/extraction")
async def reread_document(
    document_id: str, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> dict[str, Any]:
    """Re-reads one owned lecture's stored text with the current parser, only when the professor asks.

    Free of language-model calls (only the new chunks are embedded). The document, its links and its
    lecture knowledge are kept; rebuilding the knowledge is a separate, confirmed step.
    """
    document = await AssessmentContextService(db).get_document(document_id)
    if document.processing_status != "ready" or (document.mime_type or "").lower() not in _REREADABLE:
        raise ConflictError("Only a PDF or PowerPoint file that has been read can be re-read.", code="not_rereadable")
    if document.id in _reading or is_building(document.id) or is_extracting(document.id):
        raise ConflictError("This file is busy (being read or analysed). Try again when it's done.", code="busy")
    # Reserved before the first await, so a second request can't start it too.
    usage = Usage()
    with extracting(document.id):
        async with timed("source_reread", usage):
            chunks = await DocumentIngestionService(db, ai, usage).refresh_extraction(document)
    refreshed = await AssessmentContextService(db).get_document(document.id)
    return {
        "chunks": chunks,
        "pageCount": refreshed.page_count,
        "extraction": await LectureKnowledgeService(db, ai).extraction(document.id),
    }


@router.post("/documents/{document_id}/knowledge", status_code=status.HTTP_202_ACCEPTED)
async def build_document_knowledge(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
    ai: OpenAIService = Depends(get_ai),
) -> dict[str, Any]:
    """Builds (or rebuilds) one lecture's knowledge profile, only when the professor asks: it costs model calls."""
    document = await AssessmentContextService(db).get_document(document_id)
    if document.category != "course_material" or document.processing_status != "ready":
        raise ConflictError("Lecture knowledge is built for course material once it's read.", code="not_ready")
    if is_extracting(document.id):
        raise ConflictError("This file is being re-read. Build its lecture knowledge when that's done.", code="busy")
    if is_building(document.id):
        return {"state": "building"}

    async def build() -> None:
        usage = Usage()
        async with timed("lecture_knowledge", usage):
            await LectureKnowledgeService(db, ai, usage).build(document)

    background_tasks.add_task(build)
    return {"state": "building"}


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


async def _require_read_material(db: Database, ai: OpenAIService, assessment_id: str) -> None:
    """Refuses (409) while a lecture PDF isn't read yet, and starts reading it (a few seconds a file)."""
    documents = await AssessmentContextService(db).list_documents(assessment_id)
    problems = reading_problems(documents)
    if not problems:
        return

    async def work(run: RunContext) -> None:
        await run.stage("reading_files")
        await DocumentIngestionService(db, ai, run.usage).process_pending(documents)

    with contextlib.suppress(ConflictError):  # already reading this assessment's files
        await job_runner.start(db, assessment_id=assessment_id, kind="document_processing", work=work)
    raise ConflictError(
        "The course material isn't read yet: "
        + " ".join(problems)
        + " ProfPilot is reading it now; start the exam again in a moment.",
        code="material_not_ready",
    )


@router.post("/assessments/{assessment_id}/exam", status_code=status.HTTP_202_ACCEPTED)
async def create_exam(
    assessment_id: str, body: CreateExamIn, db: Database = Depends(get_db), ai: OpenAIService = Depends(get_ai)
) -> CreateExamOut:
    """Generates the full exam (a job), or starts Build with AI (an empty exam).

    Only once every lecture PDF is read: the planner is given every word of every slide, so an exam
    never starts from a lecture still being read. One that isn't read yet is read in the background,
    and the reply says so.
    """
    store = ExamStore(db)
    interpreter = PromptInterpreterService(db, ai)
    record, spec = await interpreter.require_approved(assessment_id)
    await _require_read_material(db, ai, record.id)
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
