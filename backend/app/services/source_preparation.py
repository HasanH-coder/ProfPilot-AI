"""When course material is ready for AI work, and what it still needs.

Every file is left prepared as it's read: a lecture PDF's slides' words kept page by page (its
slides are shown to the AI as it writes the exam: slides.py), a PowerPoint file's pictures read,
and its lecture knowledge built (DocumentIngestionService.prepare). Files read before that, and
files whose preparation was cut short, are brought up to date when the professor opens Sources (the
API's prepare_sources). Each step is skipped when it's already done, so checking a prepared file
costs nothing.
"""

from typing import Any

from app.services.assessment_context import DocumentRecord
from app.services.document_parsers import FIGURE_MARKER, PPTX
from app.services.lecture_knowledge import (
    extraction_state,
    is_current,
    is_legacy,
    knowledge_state,
    picture_overview,
)
from app.services.slides import has_slides


def settled(document: DocumentRecord, knowledge: dict[str, Any] | None) -> bool:
    """Whether a file is prepared, judging by its lecture knowledge alone (so most need no closer look):
    a current profile built from text with page detail (for a PowerPoint file, with every picture read)."""
    if not is_current(knowledge, document.content_sha256) or ((knowledge or {}).get("rebuild") or {}).get("status") == "failed":
        return False
    coverage = (knowledge or {}).get("coverage") or {}
    if has_slides(document):
        return bool(coverage.get("page_detail"))
    if (document.mime_type or "").lower() != PPTX:
        return True
    return bool(coverage.get("page_detail")) and "units_with_read_figures" in coverage and not coverage.get("units_with_figures")


def needs_reading(chunks: list[dict[str, Any]]) -> bool:
    """Whether a PowerPoint file's stored text should be read again: it predates page markers, a
    re-read was interrupted, or it still notes pictures nobody has read."""
    return extraction_state(chunks) in ("legacy", "interrupted") or any(
        FIGURE_MARKER.search(chunk["content"]) for chunk in chunks
    )


def needs_building(document: DocumentRecord, knowledge: dict[str, Any] | None, chunks: list[dict[str, Any]]) -> bool:
    """Whether a file's lecture knowledge should be built or rebuilt from its stored text."""
    return (
        knowledge_state(knowledge, document.content_sha256) in ("none", "outdated", "failed", "building")
        or (is_legacy(knowledge) and extraction_state(chunks) == "current")
        or picture_overview(chunks, knowledge)["afterBuild"]
        or ((knowledge or {}).get("rebuild") or {}).get("status") == "failed"
    )
