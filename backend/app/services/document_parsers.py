"""Text extraction from uploaded files, keeping where each piece of text came from.

Pure functions over bytes, so they are easy to test and can run in a worker
thread. Images, and PDF pages without a text layer (scanned pages), are read by
the AI model instead; see DocumentIngestionService.
"""

import io
import re
from dataclasses import dataclass, field

PDF = "application/pdf"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}

# A PDF page with less text than this is treated as having no text layer.
_MIN_PAGE_CHARACTERS = 25


class DocumentParseError(Exception):
    """A file couldn't be read. The message is safe to show the professor."""


@dataclass
class TextUnit:
    """A natural piece of a document: a page, a slide, or a section."""

    text: str
    page_start: int | None = None
    page_end: int | None = None
    # "Slide", "Page" or "Section", for building location labels.
    kind: str | None = None
    # A section heading, for Word documents.
    heading: str | None = None


@dataclass
class ParsedDocument:
    units: list[TextUnit]
    page_count: int | None = None
    # PDF pages (1-based) that have no text layer and should be transcribed.
    pages_without_text: list[int] = field(default_factory=list)

    @property
    def character_count(self) -> int:
        return sum(len(unit.text) for unit in self.units)


def parse_document(data: bytes, mime_type: str) -> ParsedDocument:
    if mime_type == PDF:
        return parse_pdf(data)
    if mime_type == PPTX:
        return parse_pptx(data)
    if mime_type == DOCX:
        return parse_docx(data)
    if mime_type == TXT:
        return parse_txt(data)
    raise DocumentParseError("This file type can't be read.")


def normalize_text(text: str) -> str:
    """Tidies extracted text: consistent line breaks, no runs of blank space."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf(data: bytes) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Some PDFs are "encrypted" with an empty password, only to restrict printing.
            if not reader.decrypt(""):
                raise DocumentParseError("This PDF is password-protected. Upload a copy without a password.")
        pages = list(reader.pages)
    except DocumentParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as error:
        raise DocumentParseError("This PDF couldn't be opened. It may be damaged.") from error

    units: list[TextUnit] = []
    without_text: list[int] = []
    for number, page in enumerate(pages, start=1):
        try:
            text = normalize_text(page.extract_text() or "")
        except Exception:  # a single unreadable page shouldn't sink the document
            text = ""
        if len(text) < _MIN_PAGE_CHARACTERS:
            without_text.append(number)
            continue
        units.append(TextUnit(text=text, page_start=number, page_end=number, kind="Page"))
    return ParsedDocument(units=units, page_count=len(pages), pages_without_text=without_text)


def parse_pptx(data: bytes) -> ParsedDocument:
    from pptx import Presentation

    try:
        presentation = Presentation(io.BytesIO(data))
    except Exception as error:
        raise DocumentParseError("This PowerPoint file couldn't be opened. It may be damaged.") from error

    units: list[TextUnit] = []
    slides = list(presentation.slides)
    for number, slide in enumerate(slides, start=1):
        parts: list[str] = []
        title = ""
        try:
            if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
                title = normalize_text(slide.shapes.title.text_frame.text)
        except Exception:
            title = ""
        if title:
            parts.append(title)
        for shape in slide.shapes:
            parts.extend(_shape_texts(shape, skip_text=title))
        try:
            if slide.has_notes_slide:
                notes = normalize_text(slide.notes_slide.notes_text_frame.text)
                if notes:
                    parts.append(f"Speaker notes: {notes}")
        except Exception:
            pass
        text = normalize_text("\n".join(part for part in parts if part))
        if text:
            units.append(TextUnit(text=text, page_start=number, page_end=number, kind="Slide"))
    return ParsedDocument(units=units, page_count=len(slides))


def _shape_texts(shape, *, skip_text: str = "") -> list[str]:
    texts: list[str] = []
    try:
        if shape.shape_type == 6 and hasattr(shape, "shapes"):  # a group of shapes
            for child in shape.shapes:
                texts.extend(_shape_texts(child, skip_text=skip_text))
            return texts
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            text = normalize_text(shape.text_frame.text)
            if text and text != skip_text:
                texts.append(text)
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                cells = [normalize_text(cell.text) for cell in row.cells]
                if any(cells):
                    texts.append(" | ".join(cells))
    except Exception:
        pass
    return texts


def parse_docx(data: bytes) -> ParsedDocument:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as error:
        raise DocumentParseError("This Word file couldn't be opened. It may be damaged.") from error

    units: list[TextUnit] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        text = normalize_text("\n".join(buffer))
        if text:
            units.append(TextUnit(text=text, kind="Section", heading=heading))
        buffer.clear()

    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            style = (block.style.name if block.style is not None else "") or ""
            if style.lower().startswith(("heading", "title")) and text:
                flush()
                heading = text[:200]
                buffer.append(text)
            elif text:
                buffer.append(text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = [normalize_text(cell.text) for cell in row.cells]
                # Merged cells repeat; keep each value once per row.
                unique = list(dict.fromkeys(cell for cell in cells if cell))
                if unique:
                    buffer.append(" | ".join(unique))
    flush()
    return ParsedDocument(units=units)


def parse_txt(data: bytes) -> ParsedDocument:
    text = None
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            candidate = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        text = candidate
        break
    if text is None:
        raise DocumentParseError("This text file couldn't be read.")
    text = normalize_text(text)
    units = [TextUnit(text=part) for part in re.split(r"\n{2,}", text) if part.strip()]
    return ParsedDocument(units=units)


# -----------------------------------------------------------------------------
# Chunking
# -----------------------------------------------------------------------------


@dataclass
class Chunk:
    text: str
    page_start: int | None
    page_end: int | None
    location_label: str | None


def location_label(kind: str | None, start: int | None, end: int | None, heading: str | None = None) -> str | None:
    if kind in ("Slide", "Page") and start is not None:
        if end is None or end == start:
            return f"{kind} {start}"
        return f"{kind}s {start}–{end}"
    if heading:
        return f"Section: {heading}"
    return None


def chunk_units(units: list[TextUnit], *, target_chars: int = 2400, overlap_chars: int = 300) -> list[Chunk]:
    """Groups units into retrieval-sized chunks without mixing far-apart text.

    Small consecutive units (e.g. short slides) are combined; long units are
    split at paragraph or sentence boundaries with a little overlap so no idea
    is cut in half without context.
    """
    chunks: list[Chunk] = []
    current: list[TextUnit] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if not current:
            return
        text = "\n\n".join(unit.text for unit in current)
        first, last = current[0], current[-1]
        kind = first.kind if all(unit.kind == first.kind for unit in current) else None
        start = first.page_start
        end = last.page_end if last.page_end is not None else start
        chunks.append(
            Chunk(text=text, page_start=start, page_end=end, location_label=location_label(kind, start, end, first.heading))
        )
        current, size = [], 0

    for unit in units:
        text = unit.text
        if len(text) > target_chars:
            flush()
            for piece in _split_long(text, target_chars, overlap_chars):
                chunks.append(
                    Chunk(
                        text=piece,
                        page_start=unit.page_start,
                        page_end=unit.page_end,
                        location_label=location_label(unit.kind, unit.page_start, unit.page_end, unit.heading),
                    )
                )
            continue
        # Don't merge Word sections with different headings: they are separate topics.
        if current and (size + len(text) > target_chars or unit.heading != current[-1].heading):
            flush()
        current.append(unit)
        size += len(text) + 2
    flush()
    return chunks


def _split_long(text: str, target: int, overlap: int) -> list[str]:
    paragraphs = [part for part in re.split(r"\n{2,}|\n", text) if part.strip()]
    pieces: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        for sentence in _sentences(paragraph, target):
            if buffer and len(buffer) + len(sentence) + 1 > target:
                pieces.append(buffer.strip())
                buffer = buffer[-overlap:] if overlap else ""
            buffer = f"{buffer}\n{sentence}" if buffer else sentence
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces


def _sentences(paragraph: str, target: int) -> list[str]:
    if len(paragraph) <= target:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out: list[str] = []
    for sentence in sentences:
        while len(sentence) > target:  # a very long "sentence" (e.g. a data dump)
            out.append(sentence[:target])
            sentence = sentence[target:]
        if sentence:
            out.append(sentence)
    return out


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
