"""Lecture Knowledge Profiles: a structured analysis of a whole lecture, built once and reused.

A course-material document's chunks (its full extracted text, with locations)
stay the authoritative, searchable evidence. On top of them this module builds
one reusable profile per document version:

1. The stored chunks are split into sections of at most _SEGMENT_CHARS, in
   order, so no part of a long lecture is skipped or silently truncated.
2. Each section is analysed in full (SegmentKnowledge). Every item must cite
   locations that the section really contains; a reply citing anything else is
   sent back once for repair.
3. The sections' topics are merged in code, then one short call relates them
   and titles the lecture (LectureSynthesis).
4. The profile, its coverage (which pages or slides were analysed, which had no
   text) and its token usage are saved on the document (documents.knowledge),
   together with the short summary the rest of the app shows.

A profile is reused while its version and the document's SHA-256 match. It is
never rebuilt in bulk automatically: an outdated or failed profile is rebuilt
only when the professor asks (POST /api/documents/{id}/knowledge). The Bloom's
split of any one assessment is never stored here; topics only list the levels
the material can support.
"""

import logging
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from app.ai.openai_service import OpenAIService, Usage, gather_limited
from app.ai.prompts import DataFramer, lecture_segment_instructions, lecture_synthesis_instructions
from app.core.config import settings
from app.core.errors import AppError
from app.db.supabase import Database
from app.schemas.ai import COGNITIVE_LEVELS, LectureSynthesis, SegmentKnowledge
from app.services.assessment_context import DocumentRecord
from app.services.document_parsers import EXTRACTION_GLYPHS, FIGURE_MARKER, PAGE_MARKER
from app.services.exam_store import question_texts

logger = logging.getLogger("profpilot.knowledge")

# Bump when the analysis changes enough that older profiles should be offered a rebuild.
KNOWLEDGE_VERSION = 1
# Text per analysis call: large enough for whole lecture sections, small enough
# for a complete, unhurried answer.
_SEGMENT_CHARS = 24_000
_SEGMENT_OUTPUT_TOKENS = 16_000
# Rough sizes for the cost estimate shown before a build (not a guarantee).
_INSTRUCTION_TOKENS = 1_800
# Output includes reasoning: a one-section, 39-page lecture used about 10k output tokens in all.
_SEGMENT_OUTPUT_ESTIMATE = 8_000
_SYNTHESIS_TOKENS = 4_000

# Documents whose profile this server is building right now.
_building: set[str] = set()
# Documents whose stored text this server is re-reading right now (never built at the same time).
_extracting: set[str] = set()

_ITEM_LISTS = ("concepts", "formulas", "examples", "visuals", "misconceptions", "bloom_opportunities")
_RANGE = re.compile(r"^(Slide|Page)s? (\d+)(?:\s*[–-]\s*(\d+))?$")


def is_current(knowledge: dict[str, Any] | None, content_sha256: str | None) -> bool:
    """Whether a saved profile is complete and still describes this exact document version."""
    return bool(
        knowledge
        and knowledge.get("status") == "ready"
        and knowledge.get("version") == KNOWLEDGE_VERSION
        and content_sha256
        and knowledge.get("content_sha256") == content_sha256
    )


def is_legacy(knowledge: dict[str, Any] | None) -> bool:
    """A ready profile built from text without page markers: usable, but without page-level coverage."""
    return bool(knowledge and knowledge.get("status") == "ready" and not (knowledge.get("coverage") or {}).get("page_detail"))


def extraction_state(chunks: list[dict[str, Any]]) -> str:
    """Whether a file's stored text (its chunks, in order) has page markers.

    legacy: read before page markers existed; current: every page chunk starts with one;
    interrupted: a re-read stopped between saving the new chunks and removing the old ones
    (both sets are there: pages go backwards, or marked and unmarked chunks are mixed);
    not_applicable: no page or slide locations (Word or text files).
    """
    marked = legacy = 0
    backwards = False
    previous = 0
    for chunk in chunks:
        match = _RANGE.match(chunk.get("location_label") or "")
        if not match:
            continue
        if PAGE_MARKER.match(chunk["content"]):
            marked += 1
        else:
            legacy += 1
        start = int(match.group(2))
        backwards = backwards or start < previous
        previous = start
    if backwards or (marked and legacy):
        return "interrupted"
    return "legacy" if legacy else "current" if marked else "not_applicable"


def knowledge_state(knowledge: dict[str, Any] | None, content_sha256: str | None) -> str:
    """none | building | ready | outdated | failed, for the professor."""
    if not knowledge:
        return "none"
    if is_current(knowledge, content_sha256):
        return "ready"
    if knowledge.get("status") in ("building", "failed"):
        return str(knowledge["status"])
    return "outdated"


# -----------------------------------------------------------------------------
# Building
# -----------------------------------------------------------------------------


def split_segments(chunks: list[dict[str, Any]], budget: int = _SEGMENT_CHARS) -> list[list[dict[str, Any]]]:
    """Consecutive chunks grouped into sections of at most `budget` characters (a longer chunk stands alone)."""
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for chunk in chunks:
        length = len(chunk["content"])
        if current and size + length > budget:
            segments.append(current)
            current, size = [], 0
        current.append(chunk)
        size += length
    if current:
        segments.append(current)
    return segments


def expand_location(label: str) -> list[str]:
    """'Slides 4–6' → the label itself plus 'Slide 4', 'Slide 5', 'Slide 6'."""
    match = _RANGE.match(label.strip())
    if not match:
        return [label]
    kind, start, end = match.group(1), int(match.group(2)), int(match.group(3) or match.group(2))
    return [label, *(f"{kind} {number}" for number in range(start, min(end, start + 500) + 1))]


def segment_locations(segment: list[dict[str, Any]]) -> set[str]:
    return {location for chunk in segment if chunk.get("location_label") for location in expand_location(chunk["location_label"])}


def _cited(result: SegmentKnowledge) -> set[str]:
    cited: set[str] = set()
    for topic in result.topics:
        cited.update(topic.locations)
        for name in _ITEM_LISTS:
            for item in getattr(topic, name):
                cited.update(getattr(item, "locations", []))
    return cited


def segment_problems(result: SegmentKnowledge, allowed: set[str]) -> list[str]:
    """Locations the model cited that this section doesn't contain (when it has labels at all)."""
    if not allowed:
        return []
    unknown = sorted(_cited(result) - allowed)
    if not unknown:
        return []
    shown = ", ".join(sorted(allowed, key=_location_order)[:60])
    return [f"These locations are not in this section: {', '.join(unknown[:20])}. Use only: {shown}."]


def _location_order(label: str) -> tuple[int, str]:
    match = _RANGE.match(label)
    return (int(match.group(2)) if match else 10**6, label)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def merge_topics(results: list[SegmentKnowledge]) -> list[dict[str, Any]]:
    """All sections' topics in lecture order; a topic continued in a later section is merged into one."""
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        for topic in result.topics:
            data = topic.model_dump()
            for formula in data["formulas"]:
                formula["uncertain"] = is_unverified(formula)  # PDF-letter text is never verified
            existing = merged.get(_key(topic.name))
            if existing is None:
                merged[_key(topic.name)] = data
                continue
            existing["locations"] = list(dict.fromkeys([*existing["locations"], *data["locations"]]))
            existing["summary"] = f"{existing['summary']} {data['summary']}".strip()
            for name in _ITEM_LISTS:
                existing[name].extend(data[name])
    return [{"id": f"t{number}", **topic} for number, topic in enumerate(merged.values(), start=1)]


# Characters of real content a page needs (beyond titles, headers and footers) to count as substantive.
_SUBSTANTIVE_CHARS = 40
PAGE_CLASSES = ("text", "text_with_figures", "figure_dominated", "divider", "empty")


def page_texts(chunks: list[dict[str, Any]]) -> dict[int, str]:
    """Each page's or slide's text, recovered from the markers inside the chunks (empty without markers)."""
    pages: dict[int, str] = {}
    for chunk in chunks:
        parts = PAGE_MARKER.split(chunk["content"])  # [before, kind, number, text, kind, number, text…]
        for index in range(1, len(parts) - 2, 3):
            number = int(parts[index + 1])
            pages[number] = f"{pages.get(number, '')}\n{parts[index + 2]}"
    return pages


def classify_pages(pages: dict[int, str], page_count: int | None) -> dict[str, list[int]]:
    """Every page or slide in one class of PAGE_CLASSES, plus the pages whose maths is unreliable.

    Lines repeated on at least half the pages (headers, footers, the course title), all-capital
    headings, image credits ("Adapted from …", URLs) and labels without words (axis ticks) are not
    content: a slide with only those is a divider, or figure-dominated when it also has an unread
    figure or such labels.
    """
    lines = {number: {line.strip() for line in text.splitlines() if line.strip()} for number, text in pages.items()}
    seen = Counter(line for page_lines in lines.values() for line in page_lines)
    repeated = {line for line, count in seen.items() if len(pages) >= 4 and count >= len(pages) / 2}
    classes: dict[str, list[int]] = {name: [] for name in (*PAGE_CLASSES, "uncertain_math")}
    for number in range(1, max(page_count or 0, max(pages, default=0)) + 1):
        text = pages.get(number)
        if text is None:
            classes["empty"].append(number)
            continue
        if EXTRACTION_GLYPHS.search(text):
            classes["uncertain_math"].append(number)
        figures = bool(FIGURE_MARKER.search(text))
        residual = [
            _CREDIT.sub("", line).strip()
            for line in (raw.strip() for raw in text.splitlines())
            if line and line not in repeated and not FIGURE_MARKER.match(line) and not _is_heading(line)
        ]
        prose = " ".join(line for line in residual if any(character.isalpha() for character in line))
        labels = any(line and not any(character.isalpha() for character in line) for line in residual)
        if len(prose) >= _SUBSTANTIVE_CHARS:
            classes["text_with_figures" if figures else "text"].append(number)
        elif figures or labels:
            classes["figure_dominated"].append(number)
        else:
            classes["divider"].append(number)
    return classes


# An image credit and the rest of its line ("Adapted from …", "Source: …", a bare link): never content.
_CREDIT = re.compile(
    r"(?i)(adapted\s*from|reproduced\s*from|courtesy\s*of|image\s*(?:source|credit)s?|(?<![a-z])(?:source|credit)s?\s*:"
    r"|https?://).*$"
)


def _is_heading(line: str) -> bool:
    letters = [character for character in line if character.isalpha()]
    return bool(letters) and len(line) <= 80 and not any(character.islower() for character in letters)


def coverage_of(
    chunks: list[dict[str, Any]], page_count: int | None, extracted_characters: int | None, segments: int
) -> dict[str, Any]:
    """What was analysed, page by page or slide by slide, honestly classified.

    With page markers (files read since they were added), every page is classified (see
    classify_pages) and only pages with substantive text count as analysed content. Older files
    only show which pages had any text at all; `page_detail` says which applies.
    """
    labels = [chunk["location_label"] for chunk in chunks if chunk.get("location_label")]
    covered: set[int] = set()
    kind = None
    for label in labels:
        match = _RANGE.match(label)
        if match:
            kind = match.group(1)
            start = int(match.group(2))
            covered.update(range(start, int(match.group(3) or start) + 1))
    texts = page_texts(chunks)
    marker = PAGE_MARKER.search(chunks[0]["content"]) if chunks and texts else None
    kind = marker.group(1) if marker else kind
    classes = classify_pages(texts, page_count) if texts else None
    without_text = classes["empty"] if classes else sorted(set(range(1, page_count + 1)) - covered) if page_count and kind else []
    return {
        "unit": kind.lower() if kind else None,
        "total_units": page_count,
        # Pages with any extracted text at all (titles and footers included).
        "analysed_units": (len(texts) if classes else len(covered)) if kind else None,
        # Pages with real content: None when the file predates page markers.
        "substantive_units": len(classes["text"]) + len(classes["text_with_figures"]) if classes else None,
        "page_detail": classes is not None,
        "pages": classes,
        "units_without_text": without_text,
        # Pages or slides whose pictures or charts the extracted text can't show.
        "units_with_figures": sorted({int(n) for chunk in chunks for n in FIGURE_MARKER.findall(chunk["content"])}),
        "locations": list(dict.fromkeys(labels)),
        "segments": segments,
        "characters": sum(len(chunk["content"]) for chunk in chunks),
        "truncated": bool(extracted_characters and extracted_characters >= settings.max_extracted_characters),
    }


def page_list(unit: str, numbers: list[int]) -> str:
    """'Page 29', 'Pages 1, 39', 'Pages 7–13, 15–19, 21' (consecutive numbers as ranges)."""
    numbers = sorted(set(numbers))
    runs: list[str] = []
    start = previous = numbers[0]
    for number in [*numbers[1:], None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        runs.append(str(start) if start == previous else f"{start}–{previous}")
        if number is not None:
            start = previous = number
    return f"{unit}{'s' if len(numbers) > 1 else ''} {', '.join(runs)}"


def coverage_warnings(coverage: dict[str, Any]) -> list[str]:
    warnings = []
    unit = (coverage["unit"] or "page").capitalize()
    pages = coverage.get("pages") or {}

    def plural(numbers: list[int], one: str, many: str) -> str:
        return one if len(numbers) == 1 else many

    if empty := coverage["units_without_text"]:
        warnings.append(
            f"{page_list(unit, empty)} {plural(empty, 'has', 'have')} no extractable text (usually a picture or scan): "
            f"{plural(empty, 'its', 'their')} content isn't in the profile, and questions about it need the original."
        )
    if visual := pages.get("figure_dominated"):
        warnings.append(
            f"{page_list(unit, visual)} {plural(visual, 'is', 'are')} mostly figures or images, with only titles, "
            "captions or labels as text: what they show isn't in the profile, and questions about it need the original."
        )
    figure_pages = pages.get("text_with_figures") if coverage.get("page_detail") else coverage.get("units_with_figures")
    if figure_pages:
        warnings.append(
            f"{page_list(unit, figure_pages)} also {plural(figure_pages, 'has', 'have')} figures or images that aren't "
            "in the text: the text was analysed, but questions about the visuals need the original."
        )
    if garbled := pages.get("uncertain_math"):
        warnings.append(
            f"{page_list(unit, garbled)}: the mathematics was extracted with garbled symbols, so "
            f"{plural(garbled, 'its', 'their')} formulas are unverified until checked against the original."
        )
    if coverage.get("page_detail") is False and coverage.get("total_units"):
        warnings.append(
            "Page-level coverage isn't available for this file (it was read before page markers existed): pages "
            "counted as having text include title-only and figure slides. Re-read the file for exact coverage."
        )
    if coverage["truncated"]:
        warnings.append("The file is longer than ProfPilot reads; its end wasn't extracted or analysed.")
    return warnings


# A remark about the answer format rather than the lecture (e.g. "The response schema has no examples field").
_FORMAT_REMARK = re.compile(
    r"(?i)\b(?:response|output|answer|json)\s+(?:schema|format)\b"
    r"|\bschema\s+(?:has|contains|provides|offers)\s+no\b"
    r"|\bno\s+\w+\s+field\s+(?:in|for)\s+(?:the\s+|this\s+)?(?:response|output|answer|schema)\b"
)


def material_warnings(warnings: Iterable[str]) -> list[str]:
    """The warnings about the lecture itself: remarks about ProfPilot's answer format are dropped."""
    return [warning for warning in dict.fromkeys(warnings) if not _FORMAT_REMARK.search(warning)]


def estimate_build(extracted_characters: int | None) -> dict[str, int]:
    """A rough, conservative estimate of a profile build's tokens, from the stored text size."""
    characters = extracted_characters or 0
    segments = max(1, math.ceil(characters / _SEGMENT_CHARS))
    return {
        "modelCalls": segments + 1,
        "inputTokens": characters // 4 + (segments + 1) * _INSTRUCTION_TOKENS + _SYNTHESIS_TOKENS,
        "outputTokens": segments * _SEGMENT_OUTPUT_ESTIMATE + _SYNTHESIS_TOKENS // 2,
    }


def knowledge_summary(knowledge: dict[str, Any]) -> dict[str, Any]:
    """The file summary a profile replaces (title, overview, the first topics)."""
    topics = knowledge.get("topics") or []
    return {
        "title": knowledge.get("title"),
        "summary": knowledge.get("overview"),
        "topics": [{"name": topic["name"], "location": (topic.get("locations") or [None])[0]} for topic in topics[:25]],
        "low_content": not topics,
    }


class LectureKnowledgeService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()

    async def get(self, document_id: str) -> dict[str, Any] | None:
        row = await self.db.select_one(
            "documents",
            columns="knowledge",
            filters=[("id", "eq", document_id), ("professor_id", "eq", self.db.professor_id)],
        )
        return (row or {}).get("knowledge")

    async def build(self, document: DocumentRecord) -> dict[str, Any] | None:
        """Builds and saves the profile. Never raises for a model or content problem: it saves 'failed'.

        An identical copy (same professor, same SHA-256) with a current profile is copied instead of
        analysed again, and two copies are never analysed at the same time. A ready profile stays in
        place (and in use) until its replacement is saved; a failed rebuild never removes it.
        """
        keys = {document.id, *([f"sha:{document.content_sha256}"] if document.content_sha256 else [])}
        if keys & _building or document.id in _extracting:
            return None
        _building.update(keys)
        try:
            copied = await self._copy_from_identical(document)
            return copied if copied is not None else await self._build(document)
        finally:
            _building.difference_update(keys)

    async def _copy_from_identical(self, document: DocumentRecord) -> dict[str, Any] | None:
        if not document.content_sha256:
            return None
        twins = await self.db.select(
            "documents",
            columns="id, knowledge",
            filters=[
                ("content_sha256", "eq", document.content_sha256),
                ("professor_id", "eq", self.db.professor_id),
                ("id", "neq", document.id),
                ("knowledge", "cs", {"status": "ready"}),
            ],
            limit=10,
        )
        current = [row["knowledge"] for row in twins if is_current(row.get("knowledge"), document.content_sha256)]
        # A copy without page-level detail is no better than a profile this file already has.
        has_own = (await self.get(document.id) or {}).get("status") == "ready"
        knowledge = next((k for k in current if not is_legacy(k)), None if has_own else next(iter(current), None))
        if knowledge is None:
            return None
        await self._save(document.id, knowledge, summary=knowledge_summary(knowledge))
        return knowledge

    async def _build(self, document: DocumentRecord) -> dict[str, Any]:
        started = datetime.now(UTC).isoformat()
        tokens_before = (self.usage.input_tokens, self.usage.output_tokens, self.usage.model_calls)
        base = {"version": KNOWLEDGE_VERSION, "content_sha256": document.content_sha256, "started_at": started}
        # A working profile keeps serving assessments while its replacement is built.
        previous = await self.get(document.id)
        kept = {k: v for k, v in previous.items() if k != "rebuild"} if (previous or {}).get("status") == "ready" else None
        await self._save(
            document.id,
            {**kept, "rebuild": {"status": "building", "started_at": started}} if kept else {**base, "status": "building"},
        )
        try:
            chunks = await self._chunks(document.id)
            if not chunks:
                raise AppError("This file has no extracted text to analyse.")
            if extraction_state(chunks) == "interrupted":
                raise AppError("Re-reading this file was interrupted. Re-read it again before building its lecture knowledge.")
            segments = split_segments(chunks)
            framer = DataFramer()
            results: list[SegmentKnowledge] = await gather_limited(
                settings.max_concurrent_generation_calls,
                [self._analyse(framer, document, segment, index, len(segments)) for index, segment in enumerate(segments)],
            )
            topics = merge_topics(results)
            synthesis = await self._synthesize(framer, document, topics)
            names = {_key(topic["name"]) for topic in topics}
            relations = [
                relation.model_dump()
                for relation in synthesis.relations
                if _key(relation.source_topic) in names and _key(relation.target_topic) in names
            ]
            coverage = coverage_of(chunks, document.page_count, document.extracted_characters, len(segments))
            warnings = [*coverage_warnings(coverage), *material_warnings(w for result in results for w in result.warnings)]
            if len(relations) < len(synthesis.relations):
                warnings.append(
                    f"{len(synthesis.relations) - len(relations)} relation(s) named unknown topics and were left out."
                )
            knowledge = {
                **base,
                "status": "ready",
                "built_at": datetime.now(UTC).isoformat(),
                "model": settings.reasoning_model,
                "title": synthesis.title,
                "overview": synthesis.overview,
                "sections": synthesis.sections,
                "learning_objectives": synthesis.learning_objectives,
                "topics": topics,
                "relations": relations,
                "coverage": coverage,
                "warnings": list(dict.fromkeys(warnings)),
                "stats": {"topics": len(topics), "warnings": len(set(warnings))},
                "usage": self._usage_since(tokens_before),
            }
            await self._save(document.id, knowledge, summary=knowledge_summary(knowledge))
            return knowledge
        except AppError as error:
            return await self._failed(document.id, base, kept, error.message, tokens_before)
        except Exception as error:  # an unexpected failure must not leave it "building"
            logger.warning("Lecture knowledge for %s failed: %s", document.id, type(error).__name__)
            return await self._failed(document.id, base, kept, "The lecture couldn't be analysed. Try again.", tokens_before)

    async def _chunks(self, document_id: str) -> list[dict[str, Any]]:
        return await self.db.select(
            "document_chunks",
            columns="chunk_index, content, location_label",
            filters=[("document_id", "eq", document_id), ("professor_id", "eq", self.db.professor_id)],
            order=[("chunk_index", "asc")],
        )

    async def extraction(self, document_id: str) -> str:
        """The stored text's extraction state (see extraction_state)."""
        return extraction_state(await self._chunks(document_id))

    async def _analyse(
        self, framer: DataFramer, document: DocumentRecord, segment: list[dict[str, Any]], index: int, total: int
    ) -> SegmentKnowledge:
        allowed = segment_locations(segment)
        blocks = [
            framer.block("excerpt", chunk.get("location_label") or f"part {chunk['chunk_index'] + 1}", chunk["content"])
            for chunk in segment
        ]
        header = f'LECTURE "{document.original_name}", section {index + 1} of {total}. ' + (
            f"Location labels in this section: {', '.join(sorted(allowed, key=_location_order))}." if allowed else ""
        )
        return await self.ai.parse(
            purpose="analyze_lecture_segment",
            instructions=lecture_segment_instructions(framer),
            input=header + "\n\n" + "\n\n".join(blocks),
            schema=SegmentKnowledge,
            effort="low",
            verbosity="low",
            max_output_tokens=_SEGMENT_OUTPUT_TOKENS,
            validate=lambda result: segment_problems(result, allowed),
            usage=self.usage,
        )

    async def _synthesize(self, framer: DataFramer, document: DocumentRecord, topics: list[dict[str, Any]]) -> LectureSynthesis:
        inventory = "\n".join(
            f"- {topic['name']}"
            + (f" [{topic['section']}]" if topic.get("section") else "")
            + f" ({', '.join(topic['locations'][:4])}): {topic['summary'][:300]}"
            for topic in topics
        )
        return await self.ai.parse(
            purpose="synthesize_lecture",
            instructions=lecture_synthesis_instructions(framer),
            input=framer.block("inventory", f'topics of "{document.original_name}"', inventory or "(no topics found)"),
            schema=LectureSynthesis,
            effort="low",
            verbosity="low",
            max_output_tokens=8_000,
            usage=self.usage,
        )

    def _usage_since(self, before: tuple[int, int, int]) -> dict[str, int]:
        return {
            "input_tokens": self.usage.input_tokens - before[0],
            "output_tokens": self.usage.output_tokens - before[1],
            "model_calls": self.usage.model_calls - before[2],
        }

    async def _failed(
        self,
        document_id: str,
        base: dict[str, Any],
        kept: dict[str, Any] | None,
        message: str,
        before: tuple[int, int, int],
    ) -> dict[str, Any]:
        failure = {"status": "failed", "error": message[:500], "usage": self._usage_since(before)}
        # A failed rebuild is recorded beside the working profile, which stays as it was.
        knowledge = {**kept, "rebuild": {**failure, "started_at": base["started_at"]}} if kept else {**base, **failure}
        await self._save(document_id, knowledge)
        return knowledge

    async def _save(self, document_id: str, knowledge: dict[str, Any], *, summary: dict[str, Any] | None = None) -> None:
        values: dict[str, Any] = {"knowledge": knowledge}
        if summary is not None:
            values["summary"] = summary
        await self.db.update(
            "documents",
            values,
            filters=[("id", "eq", document_id), ("professor_id", "eq", self.db.professor_id)],
            columns="id",
        )


def is_building(document_id: str) -> bool:
    return document_id in _building


def is_extracting(document_id: str) -> bool:
    return document_id in _extracting


@contextmanager
def extracting(document_id: str) -> Iterator[None]:
    """Marks a document's text as being re-read, so its profile isn't built from half-replaced chunks."""
    _extracting.add(document_id)
    try:
        yield
    finally:
        _extracting.discard(document_id)


# -----------------------------------------------------------------------------
# Using profiles in prompts
# -----------------------------------------------------------------------------


def lecture_refs(
    documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]]
) -> dict[str, tuple[DocumentRecord, dict[str, Any]]]:
    """'L1', 'L2'… for the ready course-material documents with a current profile, in a stable order."""
    lectures = [
        doc
        for doc in documents
        if doc.category == "course_material"
        and doc.processing_status == "ready"
        and is_current(knowledge.get(doc.id), doc.content_sha256)
    ]
    return {f"L{number}": (doc, knowledge[doc.id]) for number, doc in enumerate(lectures, start=1)}


def mapped_ids(documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]]) -> set[str]:
    """The documents the LECTURE KNOWLEDGE map describes (so the file overview needn't repeat them)."""
    return {doc.id for doc, _profile in lecture_refs(documents, knowledge).values()}


def topic_index(refs: dict[str, tuple[DocumentRecord, dict[str, Any]]]) -> dict[str, tuple[DocumentRecord, dict[str, Any]]]:
    """'L2.t5' → (document, topic), for every topic of every lecture."""
    return {f"{ref}.{topic['id']}": (doc, topic) for ref, (doc, profile) in refs.items() for topic in profile.get("topics", [])}


def compact_locations(locations: list[str]) -> str:
    """'Slide 3', 'Slide 4', 'Slide 5', 'Slide 9' → 'Slides 3–5, 9' (other labels kept as they are)."""
    numbers: dict[str, set[int]] = {}
    others: list[str] = []
    for label in locations:
        match = _RANGE.match(label)
        if match:
            start = int(match.group(2))
            numbers.setdefault(match.group(1), set()).update(range(start, int(match.group(3) or start) + 1))
        elif label not in others:
            others.append(label)
    parts = []
    for kind, found in numbers.items():
        ordered, runs = sorted(found), []
        for number in ordered:
            if runs and number == runs[-1][1] + 1:
                runs[-1][1] = number
            else:
                runs.append([number, number])
        text = ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)
        parts.append(f"{kind}{'s' if len(ordered) > 1 else ''} {text}")
    return "; ".join([*parts, *others])


def render_inventory(documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]], framer: DataFramer) -> str:
    """A compact map of every lecture: structure, and every topic with its id, locations and supported levels.

    It is a map, not the notes: each planned question's writer gets its topics'
    full notes (render_topics) and the retrieved excerpts. Every topic is
    always listed; only relations and warnings beyond a few are counted instead
    of listed, and the text says so.
    """
    refs = lecture_refs(documents, knowledge)
    if not refs:
        return "LECTURE KNOWLEDGE: no lecture knowledge profiles are available; rely on the overview and excerpts."
    blocks = []
    for ref, (doc, profile) in refs.items():
        lines = [f"{ref}: {profile.get('title') or doc.original_name}. {profile.get('overview', '')}"]
        if profile.get("sections"):
            lines.append("Sections: " + " → ".join(profile["sections"]))
        for topic in profile.get("topics", []):
            levels = sorted({o["level"] for o in topic.get("bloom_opportunities", [])}, key=COGNITIVE_LEVELS.index)
            extras = [
                f"{len(topic[name])} {label}{'s' if len(topic[name]) > 1 else ''}"
                for name, label in (("formulas", "formula"), ("examples", "example"))
                if topic.get(name)
            ]
            unverified = sum(1 for formula in topic.get("formulas", []) if is_unverified(formula))
            if unverified:
                extras.append(f"{unverified} unverified formula{'s' if unverified > 1 else ''}")
            if any(not visual.get("interpretable", True) for visual in topic.get("visuals", [])):
                extras.append("figure not in text")
            line = f"[{ref}.{topic['id']}] {topic['name']} ({compact_locations(topic['locations']) or 'location unknown'})"
            line += f" {' '.join(_LEVEL_CODES[level] for level in levels) or '-'}"
            if extras:
                line += f" ({', '.join(extras)})"
            lines.append(line)
        lines += _capped(
            "Relations",
            [f"{r['source_topic']} {r['kind'].replace('_', ' ')} {r['target_topic']}" for r in profile.get("relations", [])],
            _INVENTORY_RELATIONS,
        )
        lines += _capped("Warnings", material_warnings(profile.get("warnings", [])), _INVENTORY_WARNINGS)
        blocks.append(framer.block("lecture_knowledge", f'{ref} | "{doc.original_name}"', "\n".join(lines)))
    missing = [
        doc.original_name
        for doc in documents
        if doc.category == "course_material"
        and doc.processing_status == "ready"
        and doc.id not in {d.id for d, _ in refs.values()}
    ]
    note = (
        (" No profile yet for: " + "; ".join(f'"{name}"' for name in missing) + " (use the overview and excerpts).")
        if missing
        else ""
    )
    return (
        "LECTURE KNOWLEDGE (a map of each lecture, analysed in full; cite topics by their ids, e.g. L1.t3). After each "
        "topic: the Bloom's levels its material supports (R remember, U understand, Ap apply, An analyze, Ev evaluate, "
        f"Cr create).{note}\n" + "\n\n".join(blocks)
    )


_LEVEL_CODES = {"remember": "R", "understand": "U", "apply": "Ap", "analyze": "An", "evaluate": "Ev", "create": "Cr"}
_INVENTORY_RELATIONS = 12
_INVENTORY_WARNINGS = 3


def _capped(title: str, items: list[str], limit: int) -> list[str]:
    if not items:
        return []
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return [f"{title}: " + "; ".join(items[:limit]) + more]


def render_topics(
    refs: list[str], documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]], framer: DataFramer
) -> str:
    """The full knowledge of the given topics, for writing questions about them."""
    index = topic_index(lecture_refs(documents, knowledge))
    blocks = []
    for ref in dict.fromkeys(refs):
        found = index.get(ref)
        if found is None:
            continue
        doc, topic = found
        lines = [f"{topic['name']} ({', '.join(topic['locations'])}): {topic['summary']}"]
        lines += [f"- {c['kind']}: {c['name']}: {c['description']} ({', '.join(c['locations'])})" for c in topic["concepts"]]
        lines += [render_formula(formula) for formula in topic["formulas"]]
        lines += [
            f"- {e['kind'].replace('_', ' ')}: {e['title']}: {e['description']} ({', '.join(e['locations'])})"
            for e in topic["examples"]
        ]
        lines += [
            f"- figure/table{'' if v['interpretable'] else ' (content not in the text)'}: "
            f"{v['description']} ({', '.join(v['locations'])})"
            for v in topic["visuals"]
        ]
        lines += [
            f"- misconception ({'from the lecture' if m['origin'] == 'lecture' else 'ProfPilot suggestion'}): {m['description']}"
            for m in topic["misconceptions"]
        ]
        lines += [f"- {o['level']} opportunity: {o['idea']}" for o in topic["bloom_opportunities"]]
        blocks.append(framer.block("lecture_knowledge", f'{ref} | "{doc.original_name}"', "\n".join(lines)))
    if not blocks:
        return ""
    return "LECTURE KNOWLEDGE FOR THESE QUESTIONS (structured notes; the excerpts are the authoritative wording)\n" + "\n\n".join(
        blocks
    )


def is_unverified(formula: dict[str, Any]) -> bool:
    """Flagged by the analysis, or still written in a PDF's mathematical letters ('𝒛=𝒘.𝒙+𝒃'): such text
    is extraction output, never proven faithful, so it is evidence only, whatever the flag says."""
    return bool(formula.get("uncertain") or EXTRACTION_GLYPHS.search(formula.get("expression") or ""))


def render_formula(formula: dict[str, Any]) -> str:
    """One formula for a question writer: a verified one as written; an unverified one clearly marked,
    with its proposed standard form (if any) kept apart from the extracted evidence."""
    details = (f"; {', '.join(formula['variables'])}" if formula["variables"] else "") + (
        f"; note: {formula['conditions']}" if formula.get("conditions") else ""
    )
    where = f" ({', '.join(formula['locations'])})"
    if not is_unverified(formula):
        return f"- formula: {formula['name']}: {formula['expression']}{details}{where}"
    standard = (
        f"proposed standard form (ProfPilot's reading, not confirmed by the lecture): {formula['normalized']}"
        if formula.get("normalized")
        else "no reliable standard form: don't base a question on this formula's exact form"
    )
    return (
        f"- formula UNVERIFIED (extracted from the PDF, possibly garbled; never quote it): {formula['name']}: {standard}"
        f"{details}{where}. Extracted evidence, for reference only: {formula['expression']}"
    )


def unverified_expressions(documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]]) -> list[str]:
    """The extracted text of every unverified formula in these lectures' profiles."""
    return [
        formula["expression"]
        for _ref, (_doc, topic) in topic_index(lecture_refs(documents, knowledge)).items()
        for formula in topic.get("formulas", [])
        if is_unverified(formula) and formula.get("expression")
    ]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def unverified_formula_problems(content: Any, expressions: list[str], label: str = "The question") -> list[str]:
    """A question that quotes an unverified formula's garbled extracted text (a long enough piece of it)."""
    texts = _compact(" ".join(question_texts(content)))
    for expression in expressions:
        for piece in (_compact(part) for part in expression.splitlines()):
            if len(piece) >= 12 and piece in texts:
                return [
                    f"{label} quotes an unverified, garbled formula from the lecture's extracted text; use its "
                    "standard form only if one is given, otherwise test the idea without that formula."
                ]
    return []


def topic_sources(refs: list[str], documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The exact pages of these lecture topics, as stable source references for a question
    (file name and pages, which don't depend on any request's excerpt numbering)."""
    index = topic_index(lecture_refs(documents, knowledge))
    sources = []
    for ref in dict.fromkeys(refs):
        doc, topic = index.get(ref, (None, None))
        if doc is None or topic is None:
            continue
        pages = compact_locations(topic.get("locations") or [])
        label = f"{doc.original_name}, {pages}" if pages else doc.original_name
        sources.append({"document_id": doc.id, "label": label, "topic": topic["name"]})
    return sources


def unverified_refs(refs: list[str], documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]]) -> list[str]:
    """Which of these topic ids have unverified formulas, with where they are (for the professor)."""
    index = topic_index(lecture_refs(documents, knowledge))
    found = []
    for ref in dict.fromkeys(refs):
        topic = index.get(ref, (None, None))[1]
        if topic and any(is_unverified(formula) for formula in topic.get("formulas", [])):
            pages = compact_locations([loc for f in topic["formulas"] if is_unverified(f) for loc in f["locations"]])
            found.append(f"{topic['name']} ({pages or compact_locations(topic['locations'])})")
    return found


def matching_refs(text: str, documents: list[DocumentRecord], knowledge: dict[str, dict[str, Any]], limit: int = 2) -> list[str]:
    """Topic ids whose names share the most words with `text` (for a question asked for by topic)."""
    words = set(_key(text).split()) - {"the", "and", "for", "with", "question", "lecture"}
    if not words:
        return []
    scored = []
    for ref, (_doc, topic) in topic_index(lecture_refs(documents, knowledge)).items():
        overlap = len(words & set(_key(topic["name"]).split()))
        if overlap:
            scored.append((overlap, ref))
    return [ref for _score, ref in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


def overview(knowledge: dict[str, Any] | None, document: DocumentRecord) -> dict[str, Any]:
    """What the Sources page shows: status, structure, topics with locations, coverage and warnings."""
    state = knowledge_state(knowledge, document.content_sha256)
    profile = knowledge or {}
    coverage = profile.get("coverage")
    usage = profile.get("usage")
    rebuild = profile.get("rebuild")
    return {
        "state": state,
        "error": profile.get("error") if state == "failed" else None,
        # Built from text without page markers: it works, and a re-read plus rebuild adds page detail.
        "legacy": is_legacy(profile),
        # A rebuild in progress or failed beside the working profile, which stays in use meanwhile.
        "rebuild": {"state": rebuild["status"], "error": rebuild.get("error")} if rebuild else None,
        "title": profile.get("title"),
        "overview": profile.get("overview"),
        "sections": profile.get("sections", []),
        "topics": [{"id": t["id"], "name": t["name"], "locations": t["locations"][:4]} for t in profile.get("topics", [])],
        "coverage": (
            {
                "unit": coverage.get("unit"),
                "totalUnits": coverage.get("total_units"),
                "analysedUnits": coverage.get("analysed_units"),
                # None for profiles built before page markers: only "has any text" is known then.
                "substantiveUnits": coverage.get("substantive_units"),
                "pageDetail": bool(coverage.get("page_detail")),
                "pages": {_camel(name): numbers for name, numbers in (coverage.get("pages") or {}).items()},
                "unitsWithoutText": coverage.get("units_without_text", []),
                "unitsWithFigures": coverage.get("units_with_figures", []),
                "truncated": bool(coverage.get("truncated")),
            }
            if coverage
            else None
        ),
        "warnings": material_warnings(profile.get("warnings", [])),
        "usage": (
            {"inputTokens": usage["input_tokens"], "outputTokens": usage["output_tokens"], "modelCalls": usage["model_calls"]}
            if usage
            else None
        ),
        "estimate": estimate_build(document.extracted_characters),
        # Re-reading makes no language-model call: only its new chunks are embedded.
        "rereadEstimate": {"embeddingTokens": (document.extracted_characters or 0) // 4 + 10 * (document.page_count or 0)},
    }
