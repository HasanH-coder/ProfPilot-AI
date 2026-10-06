"""Test data: professors, courses, assessments and uploaded files."""

import io
import uuid
from typing import Any

from reportlab.pdfgen import canvas

from tests.fakes import FakeDatabase, FakeSupabase

PROF_A = "11111111-1111-4111-8111-111111111111"
PROF_B = "22222222-2222-4222-8222-222222222222"
TOKENS = {"token-a": PROF_A, "token-b": PROF_B}
PDF = "application/pdf"


def pdf_bytes(pages: list[str]) -> bytes:
    """A real PDF with one page per string (text layer included)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for text in pages:
        y = 800
        for line in text.split("\n"):
            pdf.drawString(40, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


async def seed_course(db: FakeDatabase, code: str = "CMPS 297U", name: str | None = "Machine Learning") -> dict[str, Any]:
    return (await db.insert("courses", {"code": code, "name": name}))[0]


async def seed_assessment(db: FakeDatabase, **setup: Any) -> dict[str, Any]:
    return (await db.insert("exam_projects", setup))[0]


async def seed_document(
    supabase: FakeSupabase,
    db: FakeDatabase,
    assessment_id: str,
    *,
    name: str = "Lecture 1.pdf",
    category: str = "course_material",
    data: bytes | None = None,
    mime: str = PDF,
) -> dict[str, Any]:
    data = (
        data
        if data is not None
        else pdf_bytes(
            [
                "Gradient descent updates parameters against the gradient.\nThe learning rate controls the step size.",
                "Linear regression minimizes squared error.\nRegularization adds a penalty.",
            ]
        )
    )
    path = f"{db.professor_id}/{assessment_id}/{category}/{uuid.uuid4()}-{name.replace(' ', '-')}"
    supabase.storage[path] = data
    return (
        await db.insert(
            "documents",
            {
                "exam_project_id": assessment_id,
                "category": category,
                "original_name": name,
                "storage_path": path,
                "mime_type": mime,
                "size_bytes": len(data),
            },
        )
    )[0]
