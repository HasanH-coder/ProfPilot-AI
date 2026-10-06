"""Turns uploaded files into searchable, summarised course knowledge.

For each file: download it (as the professor), extract its text with page,
slide or section locations, split it into chunks, embed the chunks for
semantic search, and write a short overview. Results are cached:

- a file is processed once (files are never overwritten, so they never change);
- an identical file the professor already processed elsewhere (same SHA-256)
  is copied rather than paid for again.

A file that can't be read is marked failed with a clear reason and can be
retried; it never stops the other files or the assessment.
"""

import asyncio
import base64
import hashlib
import io
import logging
from typing import Any

from pydantic import BaseModel

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer, document_summary_instructions, image_instructions, transcription_instructions
from app.core.config import settings
from app.core.errors import AIServiceError, AppError
from app.db.supabase import Database
from app.schemas.ai import DocumentSummary
from app.services.assessment_context import DOCUMENT_COLUMNS, DocumentRecord
from app.services.document_parsers import (
    IMAGE_TYPES,
    PDF,
    DocumentParseError,
    ParsedDocument,
    TextUnit,
    chunk_units,
    estimate_tokens,
    parse_document,
)
from app.services.jobs import now_iso

logger = logging.getLogger("profpilot.ingestion")

BUCKET = "assessment-files"
# Pages per transcription request for scanned PDFs.
_OCR_BATCH_PAGES = 8
_SUMMARY_CHARACTERS = 30_000
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
        """Processes every file that isn't ready yet. Returns the updated records."""
        results: list[DocumentRecord] = []
        for document in documents:
            needs_work = document.processing_status in ("pending", "processing") or (
                retry_failed and document.processing_status == "failed"
            )
            results.append(await self.process(document) if needs_work else document)
        return results

    async def process(self, document: DocumentRecord) -> DocumentRecord:
        """Processes one file. Never raises for a bad file: it is marked failed instead."""
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
        row = await self.db.select_one("documents", columns=DOCUMENT_COLUMNS, filters=[("id", "eq", document.id)])
        return DocumentRecord.from_row(row) if row else document

    # -- Steps -------------------------------------------------------------------

    async def _ingest(self, document: DocumentRecord, data: bytes, digest: str) -> None:
        parsed = await self._extract(document, data)
        if parsed.character_count == 0:
            raise DocumentParseError("No readable text was found in this file.")
        units = _trim_units(parsed.units, settings.max_extracted_characters)
        chunks = chunk_units(units)
        vectors = await self.ai.embed([chunk.text for chunk in chunks], usage=self.usage)
        summary = await self._summarize(document, units)

        await self.db.delete("document_chunks", filters=[("document_id", "eq", document.id)])
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

    async def _extract(self, document: DocumentRecord, data: bytes) -> ParsedDocument:
        mime = (document.mime_type or "").lower()
        if mime in IMAGE_TYPES:
            text = await self._read_image(document, data, mime)
            return ParsedDocument(units=[TextUnit(text=text, kind=None, heading="Image")])
        parsed = await asyncio.to_thread(parse_document, data, mime)
        if mime == PDF and parsed.pages_without_text and parsed.page_count:
            # Mostly scanned: transcribe the pages that have no text layer.
            if len(parsed.pages_without_text) >= max(1, parsed.page_count // 2):
                pages = parsed.pages_without_text[: settings.max_ocr_pages]
                transcribed = await self._transcribe_pdf_pages(document, data, pages)
                parsed.units = sorted([*parsed.units, *transcribed], key=lambda unit: unit.page_start or 0)
        return parsed

    async def _read_image(self, document: DocumentRecord, data: bytes, mime: str) -> str:
        framer = DataFramer()
        reading = await self.ai.parse_with_files(
            purpose="read_image",
            instructions=image_instructions(framer),
            text=f'The image is the uploaded file "{document.original_name}". Transcribe and describe it.',
            files=[{"type": "input_image", "image_url": _data_url(mime, data), "detail": "high"}],
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
            subset = await asyncio.to_thread(_pdf_subset, data, batch)
            result = await self.ai.parse_with_files(
                purpose="transcribe_pdf",
                instructions=transcription_instructions(framer),
                text=(
                    f'These are pages {", ".join(map(str, batch))} of "{document.original_name}", in that order. '
                    "Return one entry per page, numbering them 1, 2, 3… in the order given."
                ),
                files=[{"type": "input_file", "filename": "pages.pdf", "file_data": _data_url(PDF, subset)}],
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
            columns=DOCUMENT_COLUMNS,
            filters=[
                ("content_sha256", "eq", digest),
                ("processing_status", "eq", "ready"),
                ("professor_id", "eq", self.db.professor_id),
                ("id", "neq", document.id),
            ],
            limit=1,
        )
        if not twins:
            return False
        twin = DocumentRecord.from_row(twins[0])
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


def _data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _pdf_subset(data: bytes, pages: list[int]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    for number in pages:
        writer.add_page(reader.pages[number - 1])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


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
