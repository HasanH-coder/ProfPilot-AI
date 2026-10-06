"""Loads everything about an assessment that the AI needs, and renders it for prompts.

All reads go through the professor's own Database connection (Row Level
Security). Every query also filters by the professor's id explicitly, so a bug
can never widen access even if a policy were misconfigured.
"""

from dataclasses import dataclass, field
from typing import Any

from app.ai.prompts import DataFramer
from app.core.errors import NotFoundError
from app.db.supabase import Database
from app.domain.setup import AssessmentSetup
from app.schemas.ai import StyleProfile
from app.schemas.common import parse_id

ASSESSMENT_COLUMNS = (
    "id, status, updated_at, course_id, exam_name, duration_minutes, mcq_percentage, "
    "subjective_percentage, number_of_versions, easy_percentage, medium_percentage, "
    "hard_percentage, additional_notes, professor_prompt, enhanced_prompt, exam_spec, "
    "spec_status, spec_generated_at, spec_approved_at, generation_mode, "
    "course:courses(id, code, name)"
)

DOCUMENT_COLUMNS = (
    "id, exam_project_id, course_id, category, original_name, mime_type, size_bytes, "
    "storage_path, processing_status, processing_error, processed_at, content_sha256, "
    "page_count, extracted_characters, summary, created_at"
)

CATEGORY_LABELS = {
    "course_material": "Course material",
    "previous_exam": "Previous exam",
    "additional_attachment": "Attachment",
}


@dataclass
class CourseInfo:
    id: str
    code: str
    name: str | None

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}" if self.name else self.code


@dataclass
class AssessmentRecord:
    id: str
    setup: AssessmentSetup
    course: CourseInfo | None
    status: str
    updated_at: str
    enhanced_prompt: str | None
    exam_spec: dict[str, Any] | None
    spec_status: str
    spec_generated_at: str | None
    spec_approved_at: str | None
    generation_mode: str | None

    @property
    def title(self) -> str | None:
        return self.setup.exam_name.strip() or None


@dataclass
class DocumentRecord:
    id: str
    exam_project_id: str | None
    category: str
    original_name: str
    mime_type: str | None
    size_bytes: int | None
    storage_path: str
    processing_status: str
    processing_error: str | None
    processed_at: str | None
    content_sha256: str | None
    page_count: int | None
    extracted_characters: int | None
    summary: dict[str, Any] | None
    created_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "DocumentRecord":
        return cls(**{name: row.get(name) for name in cls.__dataclass_fields__})

    @property
    def is_image(self) -> bool:
        return (self.mime_type or "").startswith("image/")


@dataclass
class PreferenceProfile:
    explicit_notes: str | None = None
    learned: dict[str, Any] = field(default_factory=dict)
    learning_enabled: bool = True


class AssessmentContextService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_assessment(self, assessment_id: str) -> AssessmentRecord:
        assessment_id = parse_id(assessment_id, what="assessment")
        row = await self.db.select_one(
            "exam_projects",
            columns=ASSESSMENT_COLUMNS,
            filters=[("id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
        )
        if row is None:
            raise NotFoundError("This assessment doesn't exist, or you don't have access to it.")
        course = row.get("course")
        return AssessmentRecord(
            id=row["id"],
            setup=AssessmentSetup.from_row(row),
            course=CourseInfo(id=course["id"], code=course["code"], name=course.get("name")) if course else None,
            status=row["status"],
            updated_at=row["updated_at"],
            enhanced_prompt=row.get("enhanced_prompt"),
            exam_spec=row.get("exam_spec"),
            spec_status=row.get("spec_status") or "none",
            spec_generated_at=row.get("spec_generated_at"),
            spec_approved_at=row.get("spec_approved_at"),
            generation_mode=row.get("generation_mode"),
        )

    async def list_documents(self, assessment_id: str) -> list[DocumentRecord]:
        rows = await self.db.select(
            "documents",
            columns=DOCUMENT_COLUMNS,
            filters=[("exam_project_id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
            order=[("created_at", "asc")],
        )
        return [DocumentRecord.from_row(row) for row in rows]

    async def get_document(self, document_id: str) -> DocumentRecord:
        document_id = parse_id(document_id, what="file")
        row = await self.db.select_one(
            "documents",
            columns=DOCUMENT_COLUMNS,
            filters=[("id", "eq", document_id), ("professor_id", "eq", self.db.professor_id)],
        )
        if row is None:
            raise NotFoundError("This file doesn't exist, or you don't have access to it.")
        return DocumentRecord.from_row(row)

    async def get_style_profile(self, assessment_id: str) -> tuple[StyleProfile, dict[str, Any]] | None:
        row = await self.db.select_one(
            "assessment_style_profiles",
            columns="profile, source_hash, source_document_ids, updated_at",
            filters=[("exam_project_id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
        )
        if row is None:
            return None
        try:
            return StyleProfile.model_validate(row["profile"]), row
        except ValueError:
            return None

    async def get_preferences(self) -> PreferenceProfile:
        row = await self.db.select_one(
            "professor_preferences",
            columns="explicit_notes, learned, learning_enabled",
            filters=[("professor_id", "eq", self.db.professor_id)],
        )
        if row is None:
            return PreferenceProfile()
        return PreferenceProfile(
            explicit_notes=row.get("explicit_notes"),
            learned=row.get("learned") or {},
            learning_enabled=bool(row.get("learning_enabled", True)),
        )

    async def list_courses(self) -> list[CourseInfo]:
        rows = await self.db.select(
            "courses",
            columns="id, code, name",
            filters=[("professor_id", "eq", self.db.professor_id)],
            order=[("code", "asc")],
        )
        return [CourseInfo(id=row["id"], code=row["code"], name=row.get("name")) for row in rows]


# =============================================================================
# Rendering for prompts
# =============================================================================


def render_settings(record: AssessmentRecord) -> str:
    setup = record.setup
    missing = "not specified"
    lines = [
        "PROFESSOR SETTINGS (from the setup form; 'not specified' means the professor left it blank)",
        f"- Course: {record.course.label if record.course else missing}",
        f"- Assessment name: {record.title or missing}",
        f"- Duration: {setup.duration_text or missing}",
        f"- Question format (share of marks): {setup.format_text or missing}",
        f"- Difficulty (share of marks): {setup.difficulty_text or missing}",
        f"- Number of versions: {setup.number_of_versions}",
    ]
    return "\n".join(lines)


def render_professor_instructions(record: AssessmentRecord) -> str:
    prompt = record.setup.professor_prompt.strip() or "(none written)"
    notes = record.setup.additional_notes.strip() or "(none)"
    return (
        "PROFESSOR INSTRUCTIONS (the professor's own words)\n"
        f"{prompt}\n\n"
        "PROFESSOR NOTES (specific requests or constraints)\n"
        f"{notes}"
    )


def render_preferences(preferences: PreferenceProfile) -> str:
    parts: list[str] = []
    if preferences.explicit_notes and preferences.explicit_notes.strip():
        parts.append("Saved by the professor:\n" + preferences.explicit_notes.strip())
    learned = preferences.learned or {}
    if preferences.learning_enabled and learned.get("summary"):
        parts.append(
            f"Learned from {learned.get('based_on', 'their')} finalized assessment(s) and accepted revisions:\n"
            + "\n".join(f"- {line}" for line in learned["summary"])
        )
    if not parts:
        return "PROFESSOR PREFERENCES: none recorded."
    return "PROFESSOR PREFERENCES (apply gently; the current settings and instructions always override them)\n" + "\n\n".join(
        parts
    )


def render_material_overview(documents: list[DocumentRecord], framer: DataFramer) -> str:
    """Every file, by category: what it is, how long, and what it covers."""
    sections: list[str] = []
    for category in ("course_material", "previous_exam", "additional_attachment"):
        docs = [doc for doc in documents if doc.category == category]
        if not docs:
            continue
        lines: list[str] = []
        for number, doc in enumerate(docs, start=1):
            size = ""
            if doc.page_count:
                unit = "slides" if (doc.mime_type or "").endswith("presentation") else "pages"
                size = f", {doc.page_count} {unit}"
            header = f'{number}. "{doc.original_name}"{size}'
            if category == "additional_attachment":
                header += f" (attachment id {doc.id})"
            if doc.processing_status != "ready":
                lines.append(f"{header}: NOT READ ({doc.processing_error or doc.processing_status})")
                continue
            summary = doc.summary or {}
            text = summary.get("summary") or ""
            topics = summary.get("topics") or []
            topic_text = "; ".join(
                f"{topic.get('name')}" + (f" ({topic['location']})" if topic.get("location") else "")
                for topic in topics[:25]
                if isinstance(topic, dict) and topic.get("name")
            )
            line = f"{header}: {summary.get('title') or doc.original_name}. {text}"
            if topic_text:
                line += f" Topics: {topic_text}."
            lines.append(line)
        sections.append(framer.block("file_overview", CATEGORY_LABELS[category] + " files", "\n".join(lines)))
    if not sections:
        return "UPLOADED FILES: none."
    return "UPLOADED FILES (overview made from the files themselves)\n" + "\n\n".join(sections)


def render_style_profile(style: StyleProfile | None, framer: DataFramer) -> str:
    if style is None:
        return "PREVIOUS-EXAM STYLE: no previous exams were uploaded."
    return "PREVIOUS-EXAM STYLE (analysed from the professor's previous exams)\n" + framer.block(
        "style_profile", f"{style.exams_analyzed} previous exam(s)", style.model_dump_json(indent=1)
    )
