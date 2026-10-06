"""Analyses the professor's previous exams once, and keeps the result.

The style profile describes how the professor writes exams (question types,
wording, structure, difficulty, marks). It is cached per assessment with a hash
of the exact files analysed, so it is only recomputed (and paid for) when the
set of previous exams changes.
"""

import hashlib

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer, style_instructions
from app.core.config import settings
from app.db.supabase import Database
from app.schemas.ai import StyleProfile
from app.services.assessment_context import AssessmentContextService, DocumentRecord

# Characters of previous-exam text sent for analysis, shared between the exams.
_STYLE_BUDGET = 60_000


class StyleAnalysisService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()

    async def ensure_profile(self, assessment_id: str, documents: list[DocumentRecord]) -> StyleProfile | None:
        exams = [doc for doc in documents if doc.category == "previous_exam" and doc.processing_status == "ready"]
        context = AssessmentContextService(self.db)
        existing = await context.get_style_profile(assessment_id)

        if not exams:
            if existing:
                await self.db.delete(
                    "assessment_style_profiles",
                    filters=[("exam_project_id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
                )
            return None

        source_hash = hashlib.sha256("|".join(sorted(f"{doc.id}:{doc.content_sha256}" for doc in exams)).encode()).hexdigest()
        if existing and existing[1].get("source_hash") == source_hash:
            return existing[0]

        framer = DataFramer()
        per_exam = _STYLE_BUDGET // len(exams)
        blocks: list[str] = []
        for doc in exams:
            rows = await self.db.select(
                "document_chunks",
                columns="content, location_label",
                filters=[("document_id", "eq", doc.id), ("professor_id", "eq", self.db.professor_id)],
                order=[("chunk_index", "asc")],
            )
            text = "\n\n".join(row["content"] for row in rows)
            if len(text) > per_exam:
                text = text[:per_exam] + "\n[…the rest of this exam was omitted for length…]"
            blocks.append(framer.block("previous_exam", f'"{doc.original_name}"', text))

        profile = await self.ai.parse(
            purpose="analyze_previous_exams",
            instructions=style_instructions(framer),
            input=(f"Analyse these {len(exams)} previous exam(s) by the same professor.\n\n" + "\n\n".join(blocks)),
            schema=StyleProfile,
            effort="medium",
            verbosity="low",
            max_output_tokens=12_000,
            usage=self.usage,
        )
        profile.exams_analyzed = len(exams)
        values = {
            "source_hash": source_hash,
            "source_document_ids": [doc.id for doc in exams],
            "profile": profile.model_dump(),
            "model": settings.reasoning_model,
        }
        if existing:
            await self.db.update(
                "assessment_style_profiles",
                values,
                filters=[("exam_project_id", "eq", assessment_id), ("professor_id", "eq", self.db.professor_id)],
                columns="id",
            )
        else:
            await self.db.insert("assessment_style_profiles", {"exam_project_id": assessment_id, **values}, columns="id")
        return profile
