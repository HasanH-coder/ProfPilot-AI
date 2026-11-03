"""Every slide's words, and the slides themselves, for writing exams the way a chat assistant reads a PDF.

Nothing is written about a lecture's slides when it's read. The words of every page come from the
file itself (exact, at once and at no cost; a scanned page's are transcribed) and are kept page by
page in document_pages.text_layer, where they can be listed and searched. The PDF stays in Storage.

When an exam is written, each request carries what it needs, as a PDF goes with a question to a chat
assistant:
- the planner gets every word of every slide of the assessment's lectures, under short ids ('F1:p8'),
  and names the slides each question is built on (checked here: slide_problems);
- each question writer gets its questions' slides as images, rendered from the PDF there and then,
  with their words, so it sees the diagrams, tables and formulas exactly as drawn;
- the reviewer gets the same slides, to check every answer key against them.
"""

import asyncio
import io
import logging
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.ai.prompts import DataFramer
from app.core.config import settings
from app.db.supabase import Database
from app.services.assessment_context import BUCKET, DocumentRecord
from app.services.document_parsers import PDF, ParsedDocument, parse_document
from app.services.figure_reading import data_url

logger = logging.getLogger("profpilot.slides")

# A slide with fewer words teaches nothing on its own (a title, "Thank you", "Questions?").
_CONTENT_WORDS = 12
# Every word of every slide goes to the planner, up to this many characters in all; beyond it, each
# slide's words are shortened evenly and the planner is told.
_ALL_WORDS_CHARS = 320_000
# Slide images shown with one writing or review request (each costs about 1,500 tokens).
WRITER_IMAGES = 12
REVIEW_IMAGES = 16
# PDFium isn't thread-safe: one document at a time, although the requests that use them run concurrently.
_PDFIUM = threading.Lock()


def has_slides(document: DocumentRecord) -> bool:
    """A course-material PDF: its slides' words are kept page by page, and its slides shown to the AI as it writes."""
    return document.category == "course_material" and (document.mime_type or "").lower() == PDF


# -----------------------------------------------------------------------------
# Every slide's words
# -----------------------------------------------------------------------------


def page_words(parsed: ParsedDocument) -> dict[int, str]:
    """Every page's words: its own text layer or, for a scanned page, its transcription."""
    words = {number: parsed.page_texts.get(number, "") for number in range(1, (parsed.page_count or 0) + 1)}
    for unit in parsed.units:
        number = unit.page_start
        if unit.kind == "Page" and number in words and not words[number].strip():
            words[number] = unit.text
    return words


async def save_page_words(db: Database, document: DocumentRecord, parsed: ParsedDocument, *, digest: str) -> None:
    """Keeps every page's words, one row per page, in place of what was kept for the file before.

    A page with words is 'analysed' and a page without any 'blank' (the table's statuses); the number
    of pictures the parser found on a page is kept with it, so the planner knows which slides show more
    than their words.
    """
    pictures = {spot.number: len(spot.pictures) for spot in parsed.figures}
    rows = [
        {
            "document_id": document.id,
            "page_number": number,
            "status": "analysed" if text.strip() else "blank",
            "text_layer": text,
            "search_text": text.strip() or None,
            "analysis": {"pictures": pictures[number]} if number in pictures else None,
            "content_sha256": digest,
        }
        for number, text in page_words(parsed).items()
    ]
    mine = [("document_id", "eq", document.id), ("professor_id", "eq", db.professor_id)]
    await db.delete("document_pages", filters=mine)
    for start in range(0, len(rows), 100):
        await db.insert("document_pages", rows[start : start + 100], columns="id")


async def copy_page_words(db: Database, source_id: str, target_id: str) -> None:
    """An identical file's slides, copied to its copy (nothing is read again)."""
    rows = await db.select(
        "document_pages",
        columns="page_number, status, text_layer, analysis, verification, search_text, error, attempts, "
        "content_sha256, analysis_version, model",
        filters=[("document_id", "eq", source_id), ("professor_id", "eq", db.professor_id)],
    )
    await db.delete("document_pages", filters=[("document_id", "eq", target_id), ("professor_id", "eq", db.professor_id)])
    for start in range(0, len(rows), 100):
        await db.insert("document_pages", [{**row, "document_id": target_id} for row in rows[start : start + 100]], columns="id")


async def ensure_page_words(db: Database, documents: list[DocumentRecord]) -> None:
    """Keeps the slides' words of lectures read before they were kept page by page, from their stored
    PDFs (no AI), and makes those whose pages were left waiting by an earlier reader searchable."""
    lectures = [doc for doc in documents if has_slides(doc) and doc.processing_status == "ready"]
    if not lectures:
        return
    rows = await db.select(
        "document_pages",
        columns="document_id, page_number, status, text_layer",
        filters=[("document_id", "in", [doc.id for doc in lectures]), ("professor_id", "eq", db.professor_id)],
    )
    counts = Counter(row["document_id"] for row in rows)
    saved = set()
    for doc in lectures:
        if counts[doc.id] >= (doc.page_count or 1):
            continue
        try:
            data = await db.download(BUCKET, doc.storage_path)
            parsed = await asyncio.to_thread(parse_document, data, PDF)
            await save_page_words(db, doc, parsed, digest=doc.content_sha256 or "")
            saved.add(doc.id)
        except Exception as error:  # its stored text still serves; the planner just doesn't list its slides
            logger.warning("The slides of %s couldn't be kept: %s", doc.id, type(error).__name__)
    for row in rows:
        if row["status"] in ("pending", "failed") and row["document_id"] not in saved:
            text = row.get("text_layer") or ""
            await db.update(
                "document_pages",
                {"status": "analysed" if text.strip() else "blank", "search_text": text.strip() or None, "error": None},
                filters=[
                    ("document_id", "eq", row["document_id"]),
                    ("professor_id", "eq", db.professor_id),
                    ("page_number", "eq", row["page_number"]),
                ],
            )


# -----------------------------------------------------------------------------
# The slides an exam is written from
# -----------------------------------------------------------------------------


@dataclass
class SlideIndex:
    """Every slide of an assessment's lectures, under a short id ('F1:p8'), with its words."""

    files: dict[str, DocumentRecord]
    # Document id → page number → {"words", "pictures"} (see slide_entry).
    pages: dict[str, dict[int, dict[str, Any]]]

    @classmethod
    def build(cls, documents: list[DocumentRecord], pages: dict[str, dict[int, dict[str, Any]]]) -> "SlideIndex":
        files = [doc for doc in documents if pages.get(doc.id)]
        return cls(files={f"F{number}": doc for number, doc in enumerate(files, start=1)}, pages=pages)

    def __bool__(self) -> bool:
        return bool(self.files)

    @property
    def refs(self) -> dict[str, tuple[DocumentRecord, int]]:
        return {f"{ref}:p{page}": (doc, page) for ref, doc in self.files.items() for page in sorted(self.pages[doc.id])}

    def ref_of(self, document_id: str, page: int) -> str | None:
        found = (ref for ref, doc in self.files.items() if doc.id == document_id and page in self.pages[doc.id])
        return next((f"{ref}:p{page}" for ref in found), None)

    def words(self, document_id: str, page: int) -> str:
        return (self.pages.get(document_id, {}).get(page) or {}).get("words") or ""

    def content_pages(self, doc: DocumentRecord) -> list[int]:
        return [page for page, slide in sorted(self.pages[doc.id].items()) if len(slide["words"].split()) >= _CONTENT_WORDS]

    def sources(self, refs: Iterable[str]) -> list[dict[str, Any]]:
        """Stable source references of these slides, for a question ("Lecture 3.pdf, Page 8")."""
        index = self.refs
        found = [index[ref] for ref in dict.fromkeys(refs) if ref in index]
        return [{"document_id": doc.id, "label": f"{doc.original_name}, Page {page}", "page": page} for doc, page in found]


def first_line(words: str) -> str:
    """A slide's first line of words (usually its title)."""
    return next((line.strip()[:120] for line in words.splitlines() if line.strip()), "")


def _flat(words: str) -> str:
    """A slide's words on one line, its lines separated by ' / '."""
    return " / ".join(line.strip() for line in words.splitlines() if line.strip())


def render_all_slides(index: SlideIndex, framer: DataFramer) -> str:
    """Every word of every slide of every lecture, each slide under its id, for planning."""
    if not index:
        return ""
    slides = sum(len(pages) for pages in index.pages.values())
    total = sum(len(slide["words"]) for pages in index.pages.values() for slide in pages.values())
    limit = None if total <= _ALL_WORDS_CHARS else max(200, _ALL_WORDS_CHARS // max(1, slides))
    blocks = []
    for ref, doc in index.files.items():
        lines = []
        for page, slide in sorted(index.pages[doc.id].items()):
            words = _flat(slide["words"])
            if limit and len(words) > limit:
                words = words[:limit] + " …"
            shows = f" [shows {slide['pictures']} picture(s) its words don't describe]" if slide["pictures"] else ""
            lines.append(f"{ref}:p{page}{shows}: {words or '(no words)'}")
        blocks.append(framer.block("slides", f'{ref} "{doc.original_name}", {len(lines)} slides', "\n".join(lines)))
    shortened = " Long slides' words are shortened to fit." if limit else ""
    return (
        "ALL SLIDES (every word of every slide of every lecture, taken from the files themselves; cite slides "
        f"by id, e.g. F1:p8; F1, F2… number the files as listed here, not the lectures).{shortened}\n" + "\n\n".join(blocks)
    )


def render_slides(refs: Iterable[str], index: SlideIndex, framer: DataFramer) -> str:
    """The words of the slides some questions are built on (their images go with them)."""
    blocks = []
    for ref in dict.fromkeys(refs):
        found = index.refs.get(ref)
        if found is None:
            continue
        doc, page = found
        words = index.words(doc.id, page).strip() or "(no words: see its image)"
        blocks.append(framer.block("slide", f'{ref} | "{doc.original_name}", page {page}', words))
    if not blocks:
        return ""
    return "THE SLIDES OF THESE QUESTIONS (every word of each slide; its image is attached under the same id)\n" + "\n\n".join(
        blocks
    )


def question_slides(questions: list[dict[str, Any]], index: SlideIndex) -> list[tuple[int, list[str]]]:
    """Each question's source slides (by number in the exam), as slide ids."""
    cited = []
    for number, row in enumerate(questions, start=1):
        refs = [
            ref
            for source in row.get("source_refs") or []
            if source.get("page") and (ref := index.ref_of(source.get("document_id") or "", source["page"]))
        ]
        if refs:
            cited.append((number, list(dict.fromkeys(refs))))
    return cited


def render_question_sources(cited: list[tuple[int, list[str]]], index: SlideIndex, framer: DataFramer) -> str:
    """Which slides each question is built on, and their words (their images go with them), for checking answers."""
    if not cited:
        return ""
    lines = "\n".join(f"Question {number}: {', '.join(refs)}" for number, refs in cited)
    slides = render_slides([ref for _, refs in cited for ref in refs], index, framer)
    return "SOURCE SLIDES OF EACH QUESTION (check every answer key against them)\n" + lines + ("\n\n" + slides if slides else "")


def slide_problems(planned: Iterable[Any], index: SlideIndex) -> tuple[list[str], list[str]]:
    """A plan's slide citations: unknown or missing ones (to fix), and lectures whose questions all
    come from their first slides (to spread out)."""
    if not index:
        return [], []
    hard, soft = [], []
    known = index.refs
    cited: dict[str, list[int]] = {}
    for question in planned:
        pages = list(getattr(question, "source_pages", []) or [])
        unknown = [ref for ref in pages if ref not in known]
        if unknown:
            hard.append(f"Question {question.number} cites slides that aren't in ALL SLIDES: {', '.join(unknown[:6])}.")
        if not pages:
            hard.append(f"Question {question.number} cites no slides: set source_pages from ALL SLIDES.")
        for ref in pages:
            if ref in known:
                doc, page = known[ref]
                cited.setdefault(doc.id, []).append(page)
    for doc in index.files.values():
        content = index.content_pages(doc)
        pages = cited.get(doc.id, [])
        if len(content) >= 10 and len(set(pages)) >= 3 and max(pages) <= content[int(len(content) * 0.4)]:
            soft.append(
                f'Every question on "{doc.original_name}" comes from its first slides (up to page {max(pages)} of '
                f"{content[-1]}); spread them across the whole lecture."
            )
    return hard, soft


# -----------------------------------------------------------------------------
# The slides themselves, as images
# -----------------------------------------------------------------------------


def render_pages(data: bytes, numbers: list[int]) -> dict[int, bytes]:
    """These pages (1-based) as JPEG images, at PAGE_RENDER_SCALE; pages the file doesn't have are left out."""
    import pypdfium2 as pdfium

    pictures = {}
    with _PDFIUM:
        document = pdfium.PdfDocument(data)
        try:
            for number in numbers:
                if not 1 <= number <= len(document):
                    continue
                page = document[number - 1]
                try:
                    pictures[number] = page.render(scale=settings.page_render_scale).to_pil()
                finally:
                    page.close()
        finally:
            document.close()
    return {number: _jpeg(picture) for number, picture in pictures.items()}


def _jpeg(image: Any) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((settings.page_image_side, settings.page_image_side))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


async def slide_images(
    db: Database, index: SlideIndex, refs: Iterable[str], *, limit: int, files: dict[str, "asyncio.Future[bytes]"]
) -> list[tuple[str, bytes]]:
    """These slides as images (the first `limit` of them), rendered from the stored PDFs. `files` keeps
    each PDF downloaded once per exam. A slide that can't be shown is left out: its words still go."""
    chosen = [ref for ref in dict.fromkeys(refs) if ref in index.refs][:limit]
    wanted: dict[str, tuple[DocumentRecord, list[tuple[str, int]]]] = {}
    for ref in chosen:
        doc, page = index.refs[ref]
        wanted.setdefault(doc.id, (doc, []))[1].append((ref, page))
    images: dict[str, bytes] = {}
    for doc, items in wanted.values():
        try:
            download = files.get(doc.id)
            if download is None:
                download = files[doc.id] = asyncio.ensure_future(db.download(BUCKET, doc.storage_path))
            rendered = await asyncio.to_thread(render_pages, await download, [page for _, page in items])
        except Exception as error:
            logger.warning("The slides of %s couldn't be shown: %s", doc.id, type(error).__name__)
            continue
        images.update({ref: rendered[page] for ref, page in items if page in rendered})
    return [(ref, images[ref]) for ref in chosen if ref in images]


def image_parts(images: list[tuple[str, bytes]], index: SlideIndex) -> list[dict[str, Any]]:
    """Slide images as request content, each after a line naming it."""
    parts: list[dict[str, Any]] = []
    for ref, image in images:
        doc, page = index.refs[ref]
        parts.append({"type": "input_text", "text": f'Slide {ref} ("{doc.original_name}", page {page}):'})
        parts.append({"type": "input_image", "image_url": data_url("image/jpeg", image), "detail": "high"})
    return parts


# -----------------------------------------------------------------------------
# Before an exam
# -----------------------------------------------------------------------------


def reading_problems(documents: list[DocumentRecord]) -> list[str]:
    """Why the exam can't be written yet: lecture PDFs not read yet (a file that couldn't be read isn't used)."""
    return [
        f'"{doc.original_name}" is still being read.'
        for doc in documents
        if has_slides(doc) and doc.processing_status in ("pending", "processing")
    ]
