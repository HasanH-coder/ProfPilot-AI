"""Finds the parts of the uploaded material that matter for a request.

Instead of sending every page to the model, requests describe what they need
(the professor's prompt, a planned question's topic) and get back the most
relevant chunks, within a size budget. The search runs in Postgres
(match_document_chunks) as the professor, so it only ever sees their files.
"""

import asyncio
from dataclasses import dataclass

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer
from app.db.supabase import Database
from app.services.document_ingestion import vector_literal


@dataclass
class Excerpt:
    chunk_id: str
    document_id: str
    document_name: str
    location: str | None
    content: str
    similarity: float
    # A short id the model can cite, e.g. "S3". Assigned per request.
    ref: str = ""

    @property
    def label(self) -> str:
        return f"{self.document_name}, {self.location}" if self.location else self.document_name


class DocumentRetrievalService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage | None = None) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage or Usage()

    async def search(
        self,
        assessment_id: str,
        queries: list[str],
        *,
        document_names: dict[str, str],
        categories: tuple[str, ...] = ("course_material",),
        per_query: int = 8,
        max_excerpts: int = 12,
        max_chars: int = 24_000,
        exclude: set[str] | None = None,
    ) -> list[Excerpt]:
        """The most relevant chunks for any of the queries, best first."""
        queries = [query.strip()[:4000] for query in queries if query and query.strip()]
        if not queries:
            return []
        self.usage.retrievals += 1
        vectors = await self.ai.embed(queries, usage=self.usage)
        # One similarity search per query, all at once (each is a read as the professor).
        results = await asyncio.gather(
            *(
                self.db.rpc(
                    "match_document_chunks",
                    {
                        "p_exam_project_id": assessment_id,
                        "p_query_embedding": vector_literal(vector),
                        "p_categories": list(categories),
                        "p_match_count": per_query,
                    },
                )
                for vector in vectors
            )
        )
        best: dict[str, Excerpt] = {}
        for rows in results:
            for row in rows or []:
                if exclude and row["id"] in exclude:
                    continue
                similarity = float(row.get("similarity") or 0)
                current = best.get(row["id"])
                if current is None or similarity > current.similarity:
                    best[row["id"]] = Excerpt(
                        chunk_id=row["id"],
                        document_id=row["document_id"],
                        document_name=document_names.get(row["document_id"], "Uploaded file"),
                        location=row.get("location_label"),
                        content=row["content"],
                        similarity=similarity,
                    )
        ranked = sorted(best.values(), key=lambda excerpt: excerpt.similarity, reverse=True)
        return _fit(ranked, max_excerpts, max_chars)

    async def spread(
        self,
        document_ids: list[str],
        *,
        document_names: dict[str, str],
        per_document: int = 3,
        max_chars: int = 16_000,
    ) -> list[Excerpt]:
        """Evenly spaced chunks from each file, for a broad view of the material."""

        async def chosen_ids(document_id: str) -> list[str]:
            # Only the ids first: a long file has hundreds of chunks, and only a few are used.
            rows = await self.db.select(
                "document_chunks",
                columns="id, chunk_index",
                filters=[("document_id", "eq", document_id), ("professor_id", "eq", self.db.professor_id)],
                order=[("chunk_index", "asc")],
            )
            step = max(1, len(rows) // per_document)
            return [row["id"] for row in rows[::step][:per_document]]

        picked = [chunk_id for ids in await asyncio.gather(*(chosen_ids(doc) for doc in document_ids)) for chunk_id in ids]
        if not picked:
            return []
        rows = await self.db.select(
            "document_chunks",
            columns="id, document_id, chunk_index, content, location_label",
            filters=[("id", "in", picked), ("professor_id", "eq", self.db.professor_id)],
        )
        by_id = {row["id"]: row for row in rows}
        excerpts = [
            Excerpt(
                chunk_id=row["id"],
                document_id=row["document_id"],
                document_name=document_names.get(row["document_id"], "Uploaded file"),
                location=row.get("location_label"),
                content=row["content"],
                similarity=0.0,
            )
            for row in (by_id.get(chunk_id) for chunk_id in picked)
            if row is not None
        ]
        return _fit(excerpts, len(excerpts), max_chars)


def _fit(excerpts: list[Excerpt], max_excerpts: int, max_chars: int) -> list[Excerpt]:
    chosen: list[Excerpt] = []
    used = 0
    for excerpt in excerpts:
        if len(chosen) >= max_excerpts:
            break
        if used + len(excerpt.content) > max_chars and chosen:
            continue
        chosen.append(excerpt)
        used += len(excerpt.content)
    return chosen


def assign_refs(excerpts: list[Excerpt], prefix: str = "S") -> list[Excerpt]:
    for number, excerpt in enumerate(excerpts, start=1):
        excerpt.ref = f"{prefix}{number}"
    return excerpts


def render_excerpts(excerpts: list[Excerpt], framer: DataFramer, *, title: str = "COURSE MATERIAL EXCERPTS") -> str:
    if not excerpts:
        return f"{title}: none available."
    blocks = [framer.block("excerpt", f"id {excerpt.ref} | {excerpt.label}", excerpt.content) for excerpt in excerpts]
    return f"{title} (cite them by id)\n" + "\n\n".join(blocks)
