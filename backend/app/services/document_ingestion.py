"""Turns uploaded files into searchable, summarised course knowledge.

For each file: download it (as the professor), extract its text with page,
slide or section locations (a lecture PDF's slides' words are kept page by
page, and its slides are shown to the AI as it writes the exam: see slides.py;
a PowerPoint lecture's pictures are read now: see figure_reading.py), split it
into chunks, embed the chunks for semantic search, and write a short overview. Course material then
gets its Lecture Knowledge Profile (see lecture_knowledge.py), which also
provides its overview. Results are cached:

- a file is processed once (files are never overwritten, so they never change);
- an identical file the professor already processed elsewhere (same SHA-256)
  is copied rather than paid for again.

A file that can't be read is marked failed with a clear reason and can be
retried; it never stops the other files or the assessment.
"""

import asyncio
import hashlib
import logging
from typing import Any

from pydantic import BaseModel

from app.ai.openai_service import OpenAIService, Usage, gather_limited
from app.ai.prompts import DataFramer, document_summary_instructions, image_instructions, transcription_instructions
from app.core.config import settings
from app.core.errors import AIServiceError, AppError
from app.db.supabase import Database
from app.schemas.ai import DocumentSummary
from app.services.assessment_context import BUCKET, DOCUMENT_COLUMNS, DocumentRecord
from app.services.document_parsers import (
    IMAGE_TYPES,
    PDF,
    PPTX,
    DocumentParseError,
    ParsedDocument,
    TextUnit,
    chunk_units,
    estimate_tokens,
    parse_document,
    pdf_subset,
)
from app.services.figure_reading import FigureReader, data_url, with_readings
from app.services.figure_reading import plan as plan_figures
from app.services.jobs import now_iso
from app.services.lecture_knowledge import (
    LectureKnowledgeService,
    extracting,
    extraction_state,
    is_current,
    read_figure_pages,
)
from app.services.slides import copy_page_words, ensure_page_words, has_slides, save_page_words
from app.services.source_preparation import needs_building, needs_reading, settled

logger = logging.getLogger("profpilot.ingestion")

# Pages per transcription request for scanned PDFs.
_OCR_BATCH_PAGES = 8
_SUMMARY_CHARACTERS = 30_000
# A long lecture's pictures take a few goes when it's prepared (MAX_FIGURE_PAGES pages each).
_PICTURE_PASSES = 5
# Files this server is processing right now (see process).
_processing: dict[str, asyncio.Event] = {}
_INSERT_BATCH = 50


class TranscribedPage(BaseModel):
    page_number: int
    text: str


class Transcription(BaseModel):
    pages: list[TranscribedPage]


class ImageReading(BaseModel):
    transcribed_text: str
    description: str


def vector_literal(vector: list[float]) -> str:
    """pgvector's text format, e.g. "[0.12,-0.5,…]"."""
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


class DocumentIngestionService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()

    async def process_pending(self, documents: list[DocumentRecord], *, retry_failed: bool = False) -> list[DocumentRecord]:
        """Processes every file that isn't ready yet, MAX_FILES_READ_AT_ONCE at a time. Returns the
        updated records, in order."""

        async def one(document: DocumentRecord) -> DocumentRecord:
            needs_work = document.processing_status in ("pending", "processing") or (
                retry_failed and document.processing_status == "failed"
            )
            return await self.process(document) if needs_work else document

        return await gather_limited(settings.max_files_read_at_once, [one(document) for document in documents])

    async def process(self, document: DocumentRecord) -> DocumentRecord:
        """Processes one file. Never raises for a bad file: it is marked failed instead.

        A file this server is already processing (an assessment's reading job and "Improve with AI"
        can both ask for it) isn't read a second time: the second request waits for the first.
        """
        running = _processing.get(document.id)
        if running is not None:
            await running.wait()
            return await self._reload(document)
        done = _processing[document.id] = asyncio.Event()
        try:
            return await self._process(document)
        finally:
            _processing.pop(document.id, None)
            done.set()

    async def _process(self, document: DocumentRecord) -> DocumentRecord:
        await self._update(document.id, {"processing_status": "processing", "processing_error": None})
        try:
            data = await self.db.download(BUCKET, document.storage_path)
            digest = hashlib.sha256(data).hexdigest()
            if not await self._copy_from_identical(document, digest):
                await self._ingest(document, data, digest)
        except DocumentParseError as error:
            await self._fail(document, str(error))
        except AIServiceError as error:
            await self._fail(document, f"ProfPilot couldn't analyse this file right now. {error.message}")
        except AppError as error:
            await self._fail(document, error.message)
        except Exception as error:  # an unexpected parser crash on a strange file
            logger.warning("Processing %s failed: %s", document.id, type(error).__name__)
            await self._fail(document, "This file couldn't be read. It may be damaged or in an unusual format.")
        processed = await self._reload(document)
        # Course material is left ready for AI work, with no one asking: its lecture knowledge built
        # (a PowerPoint file's pictures read first). A copy of a file the professor already has reuses
        # whatever that file has (copied above) and gets the rest; a file already prepared costs nothing more.
        if processed.category == "course_material" and processed.processing_status == "ready":
            if not settled(processed, await LectureKnowledgeService(self.db, self.ai, self.usage).get(processed.id)):
                await self.prepare(processed)
                processed = await self._reload(document)  # with the summary the profile wrote
        return processed

    async def _reload(self, document: DocumentRecord) -> DocumentRecord:
        row = await self.db.select_one("documents", columns=DOCUMENT_COLUMNS, filters=[("id", "eq", document.id)])
        return DocumentRecord.from_row(row) if row else document

    async def prepare(self, document: DocumentRecord) -> None:
        """Leaves read course material ready for AI work (see source_preparation.py).

        A lecture PDF has its slides' words kept page by page (from the file, at no cost; see
        slides.py) and its stored text brought up to date if it predates page markers. A PowerPoint
        file has its pictures read (a long lecture's in a few passes). Then the lecture knowledge
        is built where needed. The file stays usable whatever happens: one that can't be re-read
        has its knowledge built from the text it has, and a failed build is saved as failed, as
        when building by hand.
        """
        knowledge = LectureKnowledgeService(self.db, self.ai, self.usage)
        if has_slides(document):
            await ensure_page_words(self.db, [document])
            if extraction_state(await knowledge.chunks(document.id)) in ("legacy", "interrupted"):
                try:
                    with extracting(document.id):
                        await self.refresh_extraction(document)
                except AppError as error:
                    logger.info("Document %s wasn't re-read: %s", document.id, error.code)
        elif (document.mime_type or "").lower() == PPTX:
            for _ in range(_PICTURE_PASSES):
                chunks = await knowledge.chunks(document.id)
                if not needs_reading(chunks):
                    break
                read = len(read_figure_pages(chunks))
                try:
                    with extracting(document.id):
                        await self.refresh_extraction(document, read_pictures=True)
                except AppError as error:
                    logger.info("Document %s wasn't re-read: %s", document.id, error.code)
                    break
                if len(read_figure_pages(await knowledge.chunks(document.id))) <= read:
                    break  # nothing more could be read now: those pictures keep their markers
        document = await self._reload(document)  # its new text size
        if needs_building(document, await knowledge.get(document.id), await knowledge.chunks(document.id)):
            await knowledge.build(document)

    # -- Steps -------------------------------------------------------------------

    async def _ingest(self, document: DocumentRecord, data: bytes, digest: str) -> None:
        parsed = await self._extract(document, data)
        if parsed.character_count == 0:
            raise DocumentParseError("No readable text was found in this file.")
        if has_slides(document):
            # A lecture's slides are shown to the AI as it writes the exam; only their words are kept now.
            await save_page_words(self.db, document, parsed, digest=digest)
        units, rows = await self._chunk_rows(document, parsed)
        # Course material's summary comes from its lecture knowledge profile (built next, from
        # the whole text), so it isn't paid for twice; other files get the short overview.
        summary = None if document.category == "course_material" else await self._summarize(document, units)

        await self.db.delete("document_chunks", filters=[("document_id", "eq", document.id)])
        for start in range(0, len(rows), _INSERT_BATCH):
            await self.db.insert("document_chunks", rows[start : start + _INSERT_BATCH], columns="id")

        await self._update(
            document.id,
            {
                "processing_status": "ready",
                "processing_error": None,
                "processed_at": now_iso(),
                "content_sha256": digest,
                "page_count": parsed.page_count,
                "extracted_characters": sum(len(unit.text) for unit in units),
                "summary": summary,
            },
        )

    async def _chunk_rows(self, document: DocumentRecord, parsed: ParsedDocument) -> tuple[list[TextUnit], list[dict[str, Any]]]:
        """The document's units (trimmed to the reading limit) and its embedded chunk rows."""
        units = _trim_units(parsed.units, settings.max_extracted_characters)
        chunks = chunk_units(units)
        vectors = await self.ai.embed([chunk.text for chunk in chunks], usage=self.usage)
        rows = [
            {
                "document_id": document.id,
                "chunk_index": index,
                "content": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "location_label": chunk.location_label,
                "token_estimate": estimate_tokens(chunk.text),
                "embedding": vector_literal(vector),
                # exam_project_id and category are filled in from the document by a trigger.
                "category": document.category,
            }
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        return units, rows

    async def refresh_extraction(self, document: DocumentRecord, *, read_pictures: bool = False) -> int:
        """Re-reads a ready file's stored text with the current parser (page markers, figure notes).

        Pictures already read are kept as they were. With read_pictures (it costs model calls), a
        PowerPoint lecture's other pictures are read too; otherwise no language-model call is made,
        and only the new chunks are embedded. A lecture PDF's slides' words are kept again. Only the
        chunks, the slides' words and the text size change: the document, its file, course and
        assessment links, summary and lecture knowledge stay as they are (the profile stays in use
        until the professor rebuilds it). The new chunks are saved before the old ones are removed,
        so search never loses the file; a failure removes what was added, and an interrupted
        re-read is cleaned up by the next one. Raises AppError, changing nothing, if the file would
        need paid transcription or no longer matches what was read. Returns the number of chunks.
        """
        data, parsed = await self._reparse(document)
        mine = [("document_id", "eq", document.id), ("professor_id", "eq", self.db.professor_id)]
        stored = await self.db.select("document_chunks", columns="chunk_index, content", filters=mine)
        kept, todo, _more = plan_figures(parsed, stored)
        readings = dict(kept)
        if read_pictures and document.category == "course_material" and not has_slides(document) and todo:
            mime = (document.mime_type or "").lower()
            readings |= await FigureReader(self.ai, self.usage).read(document.original_name, data, mime, todo)
        with_readings(parsed, readings)
        if parsed.character_count == 0:
            raise AppError("No readable text was found in this file, so it wasn't changed.")
        if has_slides(document):
            await save_page_words(self.db, document, parsed, digest=document.content_sha256 or "")
        units, rows = await self._chunk_rows(document, parsed)
        base = max((chunk["chunk_index"] for chunk in stored), default=-1) + 1
        try:
            for start in range(0, len(rows), _INSERT_BATCH):
                batch = [{**row, "chunk_index": base + row["chunk_index"]} for row in rows[start : start + _INSERT_BATCH]]
                await self.db.insert("document_chunks", batch, columns="id")
        except Exception:
            await self.db.delete("document_chunks", filters=[*mine, ("chunk_index", "gte", base)])
            raise
        # Everything before `base`: the old chunks, and any left by an interrupted re-read.
        await self.db.delete("document_chunks", filters=[*mine, ("chunk_index", "lt", base)])
        await self._update(
            document.id,
            {
                "page_count": parsed.page_count,
                "extracted_characters": sum(len(unit.text) for unit in units),
                "processed_at": now_iso(),
            },
        )
        return len(rows)

    async def _reparse(self, document: DocumentRecord) -> tuple[bytes, ParsedDocument]:
        """The stored file and its text, parsed again with the current parser.

        Raises AppError if the file no longer matches what was read, or reading it again
        would repeat a paid transcription.
        """
        mime = (document.mime_type or "").lower()
        data = await self.db.download(BUCKET, document.storage_path)
        if hashlib.sha256(data).hexdigest() != document.content_sha256:
            raise AppError("The stored file doesn't match what was read before, so it wasn't re-read.")
        try:
            parsed = await asyncio.to_thread(parse_document, data, mime)
        except DocumentParseError as error:
            raise AppError(str(error)) from error
        if _scanned_pages(parsed, mime):
            raise AppError(
                "Part of this file was transcribed by AI when it was read. Re-reading it would repeat that paid "
                "transcription, so it wasn't changed."
            )
        return data, parsed

    async def _extract(self, document: DocumentRecord, data: bytes) -> ParsedDocument:
        mime = (document.mime_type or "").lower()
        if mime in IMAGE_TYPES:
            text = await self._read_image(document, data, mime)
            return ParsedDocument(units=[TextUnit(text=text, kind=None, heading="Image")])
        parsed = await asyncio.to_thread(parse_document, data, mime)
        pages = _scanned_pages(parsed, mime)
        if pages:
            # Mostly scanned: transcribe the pages that have no text layer (with their pictures).
            transcribed = await self._transcribe_pdf_pages(document, data, pages)
            parsed.units = sorted([*parsed.units, *transcribed], key=lambda unit: unit.page_start or 0)
            parsed.figures = [spot for spot in parsed.figures if spot.number not in parsed.pages_without_text]
        if document.category == "course_material" and not has_slides(document) and parsed.figures:
            # A PowerPoint lecture's slides can't be shown to the AI later (they aren't rendered as
            # images), so its diagrams, charts, tables and code are read now. A PDF's are seen as
            # questions are written (slides.py).
            spots = parsed.figures[: settings.max_figure_pages]
            with_readings(parsed, await FigureReader(self.ai, self.usage).read(document.original_name, data, mime, spots))
        return parsed

    async def _read_image(self, document: DocumentRecord, data: bytes, mime: str) -> str:
        framer = DataFramer()
        reading = await self.ai.parse_with_files(
            purpose="read_image",
            instructions=image_instructions(framer),
            text=f'The image is the uploaded file "{document.original_name}". Transcribe and describe it.',
            files=[{"type": "input_image", "image_url": data_url(mime, data), "detail": "high"}],
            schema=ImageReading,
            usage=self.usage,
        )
        parts = [reading.description.strip()]
        if reading.transcribed_text.strip():
            parts.append("Text in the image:\n" + reading.transcribed_text.strip())
        return "\n\n".join(part for part in parts if part)

    async def _transcribe_pdf_pages(self, document: DocumentRecord, data: bytes, pages: list[int]) -> list[TextUnit]:
        framer = DataFramer()
        units: list[TextUnit] = []
        for start in range(0, len(pages), _OCR_BATCH_PAGES):
            batch = pages[start : start + _OCR_BATCH_PAGES]
            subset = await asyncio.to_thread(pdf_subset, data, batch)
            result = await self.ai.parse_with_files(
                purpose="transcribe_pdf",
                instructions=transcription_instructions(framer),
                text=(
                    f'These are pages {", ".join(map(str, batch))} of "{document.original_name}", in that order. '
                    "Return one entry per page, numbering them 1, 2, 3… in the order given."
                ),
                files=[{"type": "input_file", "filename": "pages.pdf", "file_data": data_url(PDF, subset)}],
                schema=Transcription,
                max_output_tokens=48_000,
                usage=self.usage,
            )
            for page in result.pages:
                if 1 <= page.page_number <= len(batch) and page.text.strip():
                    number = batch[page.page_number - 1]
                    units.append(TextUnit(text=page.text.strip(), page_start=number, page_end=number, kind="Page"))
        return units

    async def _summarize(self, document: DocumentRecord, units: list[TextUnit]) -> dict[str, Any]:
        framer = DataFramer()
        excerpt = _summary_excerpt(units, _SUMMARY_CHARACTERS)
        result = await self.ai.parse(
            purpose="summarize_document",
            instructions=document_summary_instructions(framer),
            input=framer.block("document", f'"{document.original_name}" ({document.category})', excerpt),
            schema=DocumentSummary,
            effort="low",
            verbosity="low",
            max_output_tokens=8_000,
            usage=self.usage,
        )
        return result.model_dump()

    async def _copy_from_identical(self, document: DocumentRecord, digest: str) -> bool:
        """Reuses the chunks, vectors and overview of an identical, already processed file."""
        twins = await self.db.select(
            "documents",
            columns=DOCUMENT_COLUMNS + ", knowledge",
            filters=[
                ("content_sha256", "eq", digest),
                ("processing_status", "eq", "ready"),
                ("professor_id", "eq", self.db.professor_id),
                ("id", "neq", document.id),
            ],
            limit=10,
        )
        if not twins:
            return False
        # Prefer a copy whose lecture knowledge profile is current, so it's reused rather than paid for again.
        best = next((row for row in twins if is_current(row.get("knowledge"), digest)), twins[0])
        twin = DocumentRecord.from_row(best)
        chunks = await self.db.select(
            "document_chunks",
            columns="chunk_index, content, page_start, page_end, location_label, token_estimate, embedding",
            filters=[("document_id", "eq", twin.id)],
            order=[("chunk_index", "asc")],
        )
        if not chunks:
            return False
        await self.db.delete("document_chunks", filters=[("document_id", "eq", document.id)])
        rows = [{**chunk, "document_id": document.id, "category": document.category} for chunk in chunks]
        for start in range(0, len(rows), _INSERT_BATCH):
            await self.db.insert("document_chunks", rows[start : start + _INSERT_BATCH], columns="id")
        if has_slides(document):  # its slides' words come with the text
            await copy_page_words(self.db, twin.id, document.id)
        # Same bytes, same lecture: its current knowledge profile is reused too, not paid for again.
        twin_knowledge = best.get("knowledge")
        await self._update(
            document.id,
            {
                "processing_status": "ready",
                "processing_error": None,
                "processed_at": now_iso(),
                "content_sha256": digest,
                "page_count": twin.page_count,
                "extracted_characters": twin.extracted_characters,
                "summary": twin.summary,
                **({"knowledge": twin_knowledge} if is_current(twin_knowledge, digest) else {}),
            },
        )
        return True

    async def _fail(self, document: DocumentRecord, message: str) -> None:
        await self._update(document.id, {"processing_status": "failed", "processing_error": message[:500]})

    async def _update(self, document_id: str, values: dict[str, Any]) -> None:
        await self.db.update(
            "documents",
            values,
            filters=[("id", "eq", document_id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )


def _scanned_pages(parsed: ParsedDocument, mime: str) -> list[int]:
    """The pages to transcribe with AI: those without a text layer, when a PDF is mostly scanned."""
    if mime != PDF or not parsed.pages_without_text or not parsed.page_count:
        return []
    if len(parsed.pages_without_text) < max(1, parsed.page_count // 2):
        return []
    return parsed.pages_without_text[: settings.max_ocr_pages]


def _trim_units(units: list[TextUnit], limit: int) -> list[TextUnit]:
    kept: list[TextUnit] = []
    total = 0
    for unit in units:
        if total >= limit:
            break
        if total + len(unit.text) > limit:
            unit = TextUnit(
                text=unit.text[: limit - total],
                page_start=unit.page_start,
                page_end=unit.page_end,
                kind=unit.kind,
                heading=unit.heading,
            )
        kept.append(unit)
        total += len(unit.text)
    return kept


def _summary_excerpt(units: list[TextUnit], budget: int) -> str:
    """The start of the document plus evenly spaced samples, with locations."""

    def render(unit: TextUnit) -> str:
        where = f"[{unit.kind} {unit.page_start}] " if unit.kind and unit.page_start else ""
        if unit.heading and not where:
            where = f"[{unit.heading}] "
        return where + unit.text

    rendered = [render(unit) for unit in units]
    if sum(len(text) for text in rendered) <= budget:
        return "\n\n".join(rendered)
    head_budget = budget // 3
    head: list[str] = []
    used = 0
    index = 0
    while index < len(rendered) and used + len(rendered[index]) <= head_budget:
        head.append(rendered[index])
        used += len(rendered[index])
        index += 1
    rest = rendered[index:]
    remaining = budget - used
    samples: list[str] = []
    if rest:
        per_sample = max(400, remaining // max(1, min(len(rest), 40)))
        step = max(1, len(rest) // max(1, remaining // per_sample))
        for position in range(0, len(rest), step):
            samples.append(rest[position][:per_sample])
            remaining -= per_sample
            if remaining <= 0:
                break
    return "\n\n".join([*head, "[…]", *samples])
