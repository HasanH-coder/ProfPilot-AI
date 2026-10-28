"""Reading the pictures in course material: diagrams, charts, tables, code and equations.

Text extraction only sees a file's text layer, so a diagram, a screenshot of code or a
table pasted as an image is invisible to every later step. The parser marks the pages or
slides whose pictures may carry content (icons, logos, the slide template, title and
closing slides are skipped; see document_parsers). Here the model is shown each such page
(a PDF page, whole) or its pictures (PowerPoint), up to eight pages per request, and writes
down what they show. Its reading replaces the page's "not in the extracted text" marker,
labelled as ProfPilot's reading of the picture, so search, lecture knowledge and question
writing all see it. Decoration is noted as such. A page that couldn't be read keeps its
marker, and reading pictures never stops a file from being read.

Readings are stored in the file's text (its chunks), so reading the file again keeps them
instead of paying for them twice (see stored_readings).
"""

import asyncio
import base64
import io
import logging
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel

from app.ai.openai_service import OpenAIService, Usage, gather_limited
from app.ai.prompts import DataFramer, figure_instructions
from app.core.config import settings
from app.services.document_parsers import (
    DECORATIVE,
    FIGURE_END,
    FIGURE_MARKER,
    PAGE_MARKER,
    PDF,
    READ_FIGURE,
    UNREAD_FIGURE_LINE,
    FigureSpot,
    ParsedDocument,
    TextUnit,
    figure_marker,
    pdf_subset,
)

logger = logging.getLogger("profpilot.figures")

# Pages per request, and pictures per request (PowerPoint, where pictures are sent one by one).
_BATCH_PAGES = 8
_BATCH_IMAGES = 16
# The model reads pictures at most this many pixels a side; larger ones are scaled down first.
_IMAGE_SIDE = 2048
_SENDABLE = {"image/png", "image/jpeg", "image/webp"}
# A PowerPoint slide's own text, shown beside its pictures for context.
_SLIDE_CONTEXT_CHARS = 800

PictureKind = Literal["diagram", "chart", "table", "code", "equation", "screenshot", "photo", "illustration", "other"]


class PictureContent(BaseModel):
    kind: PictureKind
    # Whether it carries course content (decoration such as a stock photo doesn't).
    informative: bool
    # Everything it shows, in full; empty when it isn't informative.
    content: str


class PagePictures(BaseModel):
    # 1, 2, 3… in the order the pages were given.
    page_number: int
    pictures: list[PictureContent]


class PictureReading(BaseModel):
    pages: list[PagePictures]


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class FigureReader:
    def __init__(self, ai: OpenAIService, usage: Usage) -> None:
        self.ai = ai
        self.usage = usage

    async def read(self, name: str, data: bytes, mime: str, spots: list[FigureSpot]) -> dict[int, str]:
        """Reads these pages' pictures. Returns each read page's reading (see reading_text).

        A batch the model couldn't read is left out (its pages keep their markers); it never
        raises, so a file is read even when its pictures can't be.
        """
        batches = _batches(spots, pdf=mime == PDF)
        results = await gather_limited(
            settings.max_concurrent_generation_calls, [self._read_batch(name, data, mime, batch) for batch in batches]
        )
        return {number: text for result in results for number, text in result.items()}

    async def _read_batch(self, name: str, data: bytes, mime: str, batch: list[FigureSpot]) -> dict[int, str]:
        framer = DataFramer()
        unit = batch[0].kind.lower()
        unopened: dict[int, int] = {}
        try:
            if mime == PDF:
                subset = await asyncio.to_thread(pdf_subset, data, [spot.number for spot in batch])
                files: list[dict[str, Any]] = [
                    {"type": "input_file", "filename": "pages.pdf", "file_data": data_url(PDF, subset)}
                ]
            else:
                files, unopened = await asyncio.to_thread(_slide_pictures, batch, framer)
            numbers = ", ".join(str(spot.number) for spot in batch)
            result = await self.ai.parse_with_files(
                purpose="read_figures",
                instructions=figure_instructions(framer),
                text=(
                    f'These are {unit}{"s" if len(batch) > 1 else ""} {numbers} of the lecture "{name}", in that order. '
                    "Read the pictures on each. Return one entry per page, numbering them 1, 2, 3… in the order given."
                ),
                files=files,
                schema=PictureReading,
                effort="medium",
                validate=lambda reading: _page_problems(reading, len(batch)),
                usage=self.usage,
            )
        except Exception as error:  # the pages keep their markers; the file is still read
            logger.warning("Reading pictures on %ss %s failed: %s", unit, batch[0].number, type(error).__name__)
            return {}
        readings: dict[int, str] = {}
        for page in result.pages:
            if not 1 <= page.page_number <= len(batch):
                continue
            spot = batch[page.page_number - 1]
            text = reading_text(spot, page.pictures)
            if unopened.get(spot.number):
                # Pictures in a format the model can't open (e.g. a Windows metafile) stay marked.
                text += "\n" + figure_marker(unit, spot.number, unopened[spot.number])
            readings[spot.number] = text
        return readings


def _batches(spots: list[FigureSpot], *, pdf: bool) -> list[list[FigureSpot]]:
    """Consecutive pages, at most eight per request (and, for PowerPoint, at most sixteen pictures)."""
    batches: list[list[FigureSpot]] = []
    current: list[FigureSpot] = []
    pictures = 0
    for spot in spots:
        count = 0 if pdf else len(spot.pictures)
        if current and (len(current) >= _BATCH_PAGES or pictures + count > _BATCH_IMAGES):
            batches.append(current)
            current, pictures = [], 0
        current.append(spot)
        pictures += count
    if current:
        batches.append(current)
    return batches


def _slide_pictures(batch: list[FigureSpot], framer: DataFramer) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """A PowerPoint batch as model input: each slide's text for context, then its pictures.

    Returns the input and, by slide, how many pictures couldn't be opened (left unread).
    """
    files: list[dict[str, Any]] = []
    unopened: dict[int, int] = defaultdict(int)
    for position, spot in enumerate(batch, start=1):
        context = framer.block("slide_text", f"slide {spot.number}", spot.text[:_SLIDE_CONTEXT_CHARS] or "(no text)")
        files.append({"type": "input_text", "text": f"Page {position} (slide {spot.number}). Its text:\n{context}"})
        for picture in spot.pictures:
            prepared = _prepared(*picture.image) if picture.image else None
            if prepared is None:
                unopened[spot.number] += 1
                continue
            files.append({"type": "input_image", "image_url": data_url(*prepared), "detail": "high"})
    return files, dict(unopened)


def _prepared(mime: str, data: bytes) -> tuple[str, bytes] | None:
    """A picture in a form the model reads (PNG, JPEG or WebP, at most 2048 pixels a side), or None."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            if mime in _SENDABLE and max(image.size) <= _IMAGE_SIDE:
                return mime, data
            image.thumbnail((_IMAGE_SIDE, _IMAGE_SIDE))
            converted = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG")
            return "image/png", buffer.getvalue()
    except Exception:  # e.g. a Windows metafile (EMF, WMF) or SVG: left unread
        return None


def _page_problems(reading: PictureReading, count: int) -> list[str]:
    numbers = [page.page_number for page in reading.pages]
    if sorted(numbers) == list(range(1, count + 1)):
        return []
    return [f"Return exactly one entry for each of the {count} pages, numbered 1 to {count} in the order given (not {numbers})."]


# -----------------------------------------------------------------------------
# Readings in a file's text
# -----------------------------------------------------------------------------


def reading_text(spot: FigureSpot, pictures: list[PictureContent]) -> str:
    """A page's pictures as they go in its text: each informative one in full, labelled as ProfPilot's reading."""
    unit = spot.kind.lower()
    shown = [picture for picture in pictures if picture.informative and picture.content.strip()]
    if not shown:
        return f"[Figure on {unit} {spot.number}, read by ProfPilot: {DECORATIVE}]"
    return "\n".join(
        f"[Figure on {unit} {spot.number}, read by ProfPilot: {picture.kind}]\n{_safe(picture.content)}\n{FIGURE_END}"
        for picture in shown
    )


def _safe(content: str) -> str:
    """A reading with nothing in it that could pass for a page, figure or end marker."""
    lines = []
    for line in content.replace("\x00", "").strip().splitlines():
        if PAGE_MARKER.match(line) or READ_FIGURE.match(line) or FIGURE_MARKER.match(line) or line.strip() == FIGURE_END:
            line = line.replace("[", "(").replace("]", ")")
        lines.append(line.rstrip())
    return "\n".join(lines)


def with_readings(parsed: ParsedDocument, readings: dict[int, str]) -> None:
    """Puts each read page's reading in its text, in place of its "not in the extracted text" marker.

    A page with too little text of its own to keep (a title over a diagram) is kept for its
    pictures: with their reading, or marked as not read yet. Charts whose data couldn't be read
    stay marked.
    """
    units = {unit.page_start: unit for unit in parsed.units if unit.kind in ("Page", "Slide")}
    for spot in parsed.figures:
        unit = units.get(spot.number)
        if unit is None:
            marker = figure_marker(spot.kind.lower(), spot.number, len(spot.pictures))
            unit = TextUnit(text=f"{spot.text}\n{marker}".strip(), page_start=spot.number, page_end=spot.number, kind=spot.kind)
            units[spot.number] = unit
            parsed.units.append(unit)
        reading = readings.get(spot.number)
        if reading is None:
            continue
        marker = UNREAD_FIGURE_LINE.search(unit.text)
        left = int(marker.group(1)) - len(spot.pictures) if marker else 0
        body = UNREAD_FIGURE_LINE.sub("", unit.text).strip()
        tail = f"\n{figure_marker(spot.kind.lower(), spot.number, left)}" if left > 0 else ""
        unit.text = "\n".join(part for part in (body, reading) if part) + tail
    parsed.units.sort(key=lambda unit: unit.page_start or 0)


def stored_readings(chunks: list[dict[str, Any]]) -> dict[int, str]:
    """The readings already in a file's stored text, by page, so reading it again keeps them.

    Only a page stored whole in one chunk counts (a long page split across chunks repeats
    some text), and only when all its pictures were read (no marker left).
    """
    texts: dict[int, set[str]] = defaultdict(set)
    for chunk in chunks:
        parts = PAGE_MARKER.split(chunk["content"])  # [before, kind, number, text, kind, number, text…]
        for index in range(1, len(parts) - 2, 3):
            texts[int(parts[index + 1])].add(parts[index + 2].strip())
    readings: dict[int, str] = {}
    for number, found in texts.items():
        if len(found) != 1:
            continue
        text = next(iter(found))
        if UNREAD_FIGURE_LINE.search(text):
            continue
        blocks = _blocks(text)
        if blocks:
            readings[number] = "\n".join(blocks)
    return readings


def _blocks(text: str) -> list[str]:
    """The reading blocks in a page's text; none if one is incomplete (the page is then read again)."""
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        match = READ_FIGURE.match(lines[index])
        if not match:
            index += 1
            continue
        if match.group(3) == DECORATIVE:
            blocks.append(lines[index])
            index += 1
            continue
        end = next((later for later in range(index + 1, len(lines)) if lines[later] == FIGURE_END), None)
        if end is None:
            return []
        blocks.append("\n".join(lines[index : end + 1]))
        index = end + 1
    return blocks


def plan(parsed: ParsedDocument, chunks: list[dict[str, Any]]) -> tuple[dict[int, str], list[FigureSpot], int]:
    """For reading a file's pictures again: the readings to keep, the pages still to read (at most
    MAX_FIGURE_PAGES per go), and how many more pages are over that limit."""
    previous = stored_readings(chunks)
    kept = {spot.number: previous[spot.number] for spot in parsed.figures if spot.number in previous}
    todo = [spot for spot in parsed.figures if spot.number not in kept]
    return kept, todo[: settings.max_figure_pages], max(0, len(todo) - settings.max_figure_pages)
