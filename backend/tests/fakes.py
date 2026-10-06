"""In-memory stand-ins for Supabase and OpenAI, so tests never touch the network.

FakeDatabase implements the same `Database` interface the services use, and
imitates the parts of Postgres the code relies on:

- Row Level Security: each professor only sees and changes their own rows, and
  can only link rows to parents they own (a violation looks like "not found");
- the constraints the app depends on (one running job per kind, one exam per
  assessment, an MCQ's answer must be one of its choices, unique positions…);
- the triggers (ExamSpec staleness, chunk scope) and cascading deletes;
- the two RPCs (match_document_chunks, reorder_exam_questions) and Storage.

FakeAI implements the OpenAIService methods. Tests register a handler per
"purpose" that returns the structured object the model would; every call is
recorded so tests can check exactly what context the model was given.
"""

import copy
import hashlib
import itertools
import math
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.openai_service import ToolCall, ToolLoopResult, Usage
from app.core.errors import AIServiceError, ConflictError, InvalidInputError, NotFoundError

_clock = itertools.count()


def _now() -> str:
    # Strictly increasing timestamps, so "order by created_at" is deterministic.
    return (datetime(2026, 10, 6, tzinfo=UTC) + timedelta(microseconds=next(_clock))).isoformat()


OWNED = {
    "courses",
    "exam_projects",
    "documents",
    "document_chunks",
    "assessment_style_profiles",
    "exams",
    "exam_versions",
    "exam_sections",
    "exam_questions",
    "question_revisions",
    "ai_runs",
    "professor_preferences",
    "preference_signals",
    "exam_builder_messages",
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "courses": {"name": None, "description": None},
    "exam_projects": {
        "status": "draft",
        "course_id": None,
        "exam_name": None,
        "duration_minutes": None,
        "mcq_percentage": None,
        "subjective_percentage": None,
        "number_of_versions": 1,
        "easy_percentage": None,
        "medium_percentage": None,
        "hard_percentage": None,
        "additional_notes": None,
        "professor_prompt": None,
        "enhanced_prompt": None,
        "exam_spec": None,
        "generation_mode": None,
        "spec_status": "none",
        "spec_generated_at": None,
        "spec_approved_at": None,
    },
    "documents": {
        "course_id": None,
        "mime_type": None,
        "size_bytes": None,
        "processing_status": "pending",
        "processing_error": None,
        "processed_at": None,
        "content_sha256": None,
        "page_count": None,
        "extracted_characters": None,
        "summary": None,
    },
    "document_chunks": {"page_start": None, "page_end": None, "location_label": None, "token_estimate": None, "embedding": None},
    "assessment_style_profiles": {"model": None},
    "exams": {
        "status": "generating",
        "enhanced_prompt_snapshot": None,
        "plan": None,
        "review": None,
        "review_status": "not_run",
        "reviewed_at": None,
        "error_message": None,
        "finalized_at": None,
    },
    "exam_versions": {},
    "exam_sections": {"instructions": None},
    "exam_questions": {
        "section_id": None,
        "choices": None,
        "correct_choice": None,
        "subparts": None,
        "answer": None,
        "solution": None,
        "rubric": None,
        "explanation": None,
        "concepts": [],
        "learning_objectives": [],
        "source_refs": [],
        "figure_document_id": None,
        "estimated_minutes": None,
        "status": "draft",
        "needs_solution_review": False,
    },
    "question_revisions": {"instruction": None},
    "ai_runs": {
        "exam_id": None,
        "status": "running",
        "stage": None,
        "error_message": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "finished_at": None,
    },
    "professor_preferences": {"explicit_notes": None, "learned": {}, "learning_enabled": True},
    "preference_signals": {"exam_project_id": None},
    "exam_builder_messages": {"channel": "text"},
}

# Columns that point at a parent the professor must own (Row Level Security "with check").
PARENTS = {
    "course_id": "courses",
    "exam_project_id": "exam_projects",
    "exam_id": "exams",
    "question_id": "exam_questions",
    "document_id": "documents",
    "version_id": "exam_versions",
    "section_id": "exam_sections",
    "figure_document_id": "documents",
}

SETUP_COLUMNS = (
    "course_id",
    "exam_name",
    "duration_minutes",
    "mcq_percentage",
    "subjective_percentage",
    "number_of_versions",
    "easy_percentage",
    "medium_percentage",
    "hard_percentage",
    "additional_notes",
    "professor_prompt",
)

# table -> [(child table, foreign key column, "cascade" | "set null")]
CASCADES = {
    "exam_projects": [
        ("documents", "exam_project_id", "cascade"),
        ("exams", "exam_project_id", "cascade"),
        ("ai_runs", "exam_project_id", "cascade"),
        ("assessment_style_profiles", "exam_project_id", "cascade"),
        ("preference_signals", "exam_project_id", "set null"),
    ],
    "documents": [("document_chunks", "document_id", "cascade"), ("exam_questions", "figure_document_id", "set null")],
    "exams": [
        ("exam_versions", "exam_id", "cascade"),
        ("exam_sections", "exam_id", "cascade"),
        ("exam_questions", "exam_id", "cascade"),
        ("exam_builder_messages", "exam_id", "cascade"),
        ("ai_runs", "exam_id", "cascade"),
    ],
    "exam_questions": [("question_revisions", "question_id", "cascade")],
}


class FakeSupabase:
    """The shared state all professors' FakeDatabase connections see."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.storage: dict[str, bytes] = {}

    def connect(self, professor_id: str) -> "FakeDatabase":
        return FakeDatabase(self, professor_id)


class FakeDatabase:
    def __init__(self, supabase: FakeSupabase, professor_id: str) -> None:
        self.supabase = supabase
        self.professor_id = professor_id

    # -- Visibility ("Row Level Security") ------------------------------------------------

    def _visible(self, table: str) -> list[dict[str, Any]]:
        rows = self.supabase.tables[table]
        if table in OWNED:
            return [row for row in rows if row.get("professor_id") == self.professor_id]
        return rows

    def _owned(self, table: str, row_id: Any) -> dict[str, Any] | None:
        return next(
            (row for row in self._visible(table) if row["id" if table != "professor_preferences" else "professor_id"] == row_id),
            None,
        )

    # -- Database interface ----------------------------------------------------------------------

    async def select(self, table, *, columns="*", filters=(), order=(), limit=None):
        rows = [row for row in self._visible(table) if _matches(row, filters)]
        for column, direction in reversed(list(order)):
            rows.sort(key=lambda row, c=column: (row.get(c) is None, row.get(c)), reverse=direction == "desc")
        if limit is not None:
            rows = rows[:limit]
        return [self._project(table, row, columns) for row in rows]

    async def select_one(self, table, *, columns="*", filters=()):
        rows = await self.select(table, columns=columns, filters=filters, limit=2)
        if len(rows) > 1:
            raise AssertionError(f"select_one on {table} matched several rows")
        return rows[0] if rows else None

    async def insert(self, table, rows, *, columns="*"):
        payload = rows if isinstance(rows, list) else [rows]
        created = []
        for values in payload:
            row = {**copy.deepcopy(DEFAULTS.get(table, {})), **copy.deepcopy(values)}
            if table in OWNED:
                row.setdefault("professor_id", self.professor_id)
                if row["professor_id"] != self.professor_id:
                    raise NotFoundError()
            if table != "professor_preferences":
                row.setdefault("id", str(uuid.uuid4()))
            elif any(r["professor_id"] == row["professor_id"] for r in self.supabase.tables[table]):
                raise ConflictError("This already exists.")
            row.setdefault("created_at", _now())
            row.setdefault("updated_at", row["created_at"])
            if table == "ai_runs":
                row.setdefault("started_at", _now())
                row.setdefault("heartbeat_at", row["started_at"])
            if table == "exam_questions":
                row.setdefault("slot_id", str(uuid.uuid4()))
            self._check_parents(table, row)
            self._before_insert(table, row)
            self._check_constraints(table, row)
            self.supabase.tables[table].append(row)
            self._after_insert(table, row)
            created.append(self._project(table, row, columns))
        return created

    async def update(self, table, values, *, filters, columns="*"):
        if not filters:
            raise ValueError("update needs a filter")
        updated = []
        for row in [row for row in self._visible(table) if _matches(row, filters)]:
            candidate = {**row, **copy.deepcopy(values)}
            if table in OWNED and candidate.get("professor_id") != self.professor_id:
                raise NotFoundError()
            self._check_parents(table, candidate, changed=set(values))
            if table == "exam_projects":
                _stale_trigger(row, candidate)
            if "updated_at" in row:
                candidate["updated_at"] = _now()
            self._check_constraints(table, candidate, replacing=row)
            row.clear()
            row.update(candidate)
            updated.append(self._project(table, row, columns))
        return updated

    async def delete(self, table, *, filters):
        if not filters:
            raise ValueError("delete needs a filter")
        doomed = [row for row in self._visible(table) if _matches(row, filters)]
        for row in doomed:
            self._remove(table, row)
        return [{"id": row.get("id")} for row in doomed]

    async def rpc(self, function, params):
        if function == "match_document_chunks":
            query = _vector(params["p_query_embedding"])
            scored = []
            for chunk in self._visible("document_chunks"):
                if chunk["exam_project_id"] != params["p_exam_project_id"] or chunk["category"] not in params["p_categories"]:
                    continue
                if chunk.get("embedding") is None:
                    continue
                scored.append(
                    {
                        **_pick(
                            chunk, ("id", "document_id", "chunk_index", "content", "page_start", "page_end", "location_label")
                        ),
                        "similarity": _cosine(query, _vector(chunk["embedding"])),
                    }
                )
            scored.sort(key=lambda item: item["similarity"], reverse=True)
            return scored[: min(max(params.get("p_match_count", 12), 1), 50)]
        if function == "reorder_exam_questions":
            ids = params["p_question_ids"]
            questions = [q for q in self._visible("exam_questions") if q["version_id"] == params["p_version_id"]]
            if len(set(ids)) != len(ids) or {q["id"] for q in questions} != set(ids):
                raise InvalidInputError()
            by_id = {q["id"]: q for q in questions}
            for position, question_id in enumerate(ids, start=1):
                by_id[question_id]["position"] = position
            return None
        raise AssertionError(f"unexpected rpc {function}")

    async def download(self, bucket, path):
        if not path.startswith(f"{self.professor_id}/") or path not in self.supabase.storage:
            raise NotFoundError("This file couldn't be found.")
        return self.supabase.storage[path]

    # -- Imitated Postgres behaviour ------------------------------------------------------------

    def _project(self, table: str, row: dict[str, Any], columns: str) -> dict[str, Any]:
        result = copy.deepcopy(row)
        if table == "exam_projects" and "course:courses(" in columns:
            course = self._owned("courses", row.get("course_id")) if row.get("course_id") else None
            result["course"] = _pick(course, ("id", "code", "name")) if course else None
        return result

    def _check_parents(self, table: str, row: dict[str, Any], changed: set[str] | None = None) -> None:
        for column, parent in PARENTS.items():
            if column not in row or row[column] is None or (changed is not None and column not in changed):
                continue
            if table == "exam_versions" and column == "version_id":
                continue
            if self._owned(parent, row[column]) is None:
                raise NotFoundError()
        if table == "exam_questions":
            version = self._owned("exam_versions", row["version_id"])
            if version is None or version["exam_id"] != row["exam_id"]:
                raise NotFoundError("Something this refers to doesn't exist any more.")
            if row.get("section_id"):
                section = self._owned("exam_sections", row["section_id"])
                if section is None or section["exam_id"] != row["exam_id"]:
                    raise NotFoundError("Something this refers to doesn't exist any more.")

    def _before_insert(self, table: str, row: dict[str, Any]) -> None:
        if table == "document_chunks":
            document = self._owned("documents", row["document_id"])
            if document is None:
                raise NotFoundError()
            row["exam_project_id"] = document["exam_project_id"]
            row["category"] = document["category"]

    def _after_insert(self, table: str, row: dict[str, Any]) -> None:
        if table == "documents" and row.get("exam_project_id"):
            _mark_stale(self.supabase, row["exam_project_id"])

    def _check_constraints(self, table: str, row: dict[str, Any], replacing: dict[str, Any] | None = None) -> None:
        others = [r for r in self.supabase.tables[table] if r is not replacing]
        if table == "exams" and any(r["exam_project_id"] == row["exam_project_id"] for r in others):
            raise ConflictError("This already exists.")
        if (
            table == "ai_runs"
            and row["status"] == "running"
            and any(
                r["status"] == "running" and r["exam_project_id"] == row["exam_project_id"] and r["kind"] == row["kind"]
                for r in others
            )
        ):
            raise ConflictError("This already exists.")
        if table == "document_chunks" and any(
            r["document_id"] == row["document_id"] and r["chunk_index"] == row["chunk_index"] for r in others
        ):
            raise ConflictError("This already exists.")
        if table == "question_revisions" and any(
            r["question_id"] == row["question_id"] and r["revision_number"] == row["revision_number"] for r in others
        ):
            raise ConflictError("This already exists.")
        if table == "exam_projects" and row.get("exam_name") is not None and not str(row["exam_name"]).strip():
            raise InvalidInputError()
        if table == "exam_questions":
            _check_question(row)
            if any(r["version_id"] == row["version_id"] and r["slot_id"] == row["slot_id"] for r in others):
                raise ConflictError("This already exists.")

    def _remove(self, table: str, row: dict[str, Any]) -> None:
        rows = self.supabase.tables[table]
        if not any(r is row for r in rows):
            return
        self.supabase.tables[table] = [r for r in rows if r is not row]
        for child, column, action in CASCADES.get(table, []):
            for child_row in [r for r in self.supabase.tables[child] if r.get(column) == row.get("id")]:
                if action == "cascade":
                    self._remove(child, child_row)
                else:
                    child_row[column] = None
        if table == "documents" and row.get("exam_project_id"):
            _mark_stale(self.supabase, row["exam_project_id"])


def _check_question(row: dict[str, Any]) -> None:
    if row["type"] not in ("mcq", "short_answer", "long_answer", "problem"):
        raise InvalidInputError()
    if row["difficulty"] not in ("easy", "medium", "hard") or row["status"] not in ("draft", "approved"):
        raise InvalidInputError()
    if not 0 < float(row["points"]) <= 1000 or not row["prompt"]:
        raise InvalidInputError()
    if row["type"] == "mcq":
        choices = row.get("choices") or []
        if not 2 <= len(choices) <= 8 or row.get("correct_choice") not in [c.get("id") for c in choices]:
            raise InvalidInputError()
    elif row.get("choices") is not None or row.get("correct_choice") is not None:
        raise InvalidInputError()


def _stale_trigger(old: dict[str, Any], new: dict[str, Any]) -> None:
    if (
        old.get("spec_status") in ("draft", "approved")
        and new.get("spec_status") == old.get("spec_status")
        and any(old.get(column) != new.get(column) for column in SETUP_COLUMNS)
    ):
        new["spec_status"] = "stale"


def _mark_stale(supabase: FakeSupabase, project_id: str) -> None:
    for project in supabase.tables["exam_projects"]:
        if project["id"] == project_id and project.get("spec_status") in ("draft", "approved"):
            project["spec_status"] = "stale"


def _matches(row: dict[str, Any], filters: Sequence[tuple[str, str, Any]]) -> bool:
    for column, operator, value in filters:
        current = row.get(column)
        if operator == "eq":
            ok = current == value or (current is not None and str(current) == str(value))
        elif operator == "neq":
            ok = current != value
        elif operator == "in":
            ok = current in value
        elif operator == "is":
            ok = current is value if value is None else current == value
        elif operator in ("gt", "gte", "lt", "lte"):
            ok = (
                current is not None
                and {"gt": current > value, "gte": current >= value, "lt": current < value, "lte": current <= value}[operator]
            )
        elif operator == "cs":
            ok = isinstance(current, dict) and all(current.get(key) == item for key, item in value.items())
        else:
            raise AssertionError(f"unsupported operator {operator}")
        if not ok:
            return False
    return True


def _pick(row: dict[str, Any] | None, keys: Sequence[str]) -> dict[str, Any]:
    return {key: (row or {}).get(key) for key in keys}


def _vector(value: Any) -> list[float]:
    if isinstance(value, str):
        return [float(part) for part in value.strip("[]").split(",") if part]
    return list(value)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


# =============================================================================
# FakeAI
# =============================================================================


def embed_text(text: str, dimensions: int = 1536) -> list[float]:
    """A deterministic bag-of-words vector: texts sharing words are similar."""
    vector = [0.0] * dimensions
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 3:
            continue
        vector[int(hashlib.sha1(word.encode()).hexdigest(), 16) % dimensions] += 1.0
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


Handler = Callable[[dict[str, Any]], Any]


class FakeAI:
    """Implements the OpenAIService methods with scripted, recorded responses."""

    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {}
        self.tool_scripts: dict[str, Callable[[list[dict[str, Any]]], tuple[list[tuple[str, dict[str, Any]]], str]]] = {}
        self.calls: list[dict[str, Any]] = []
        self.embedded_texts: list[str] = []
        self.secrets: list[dict[str, Any]] = []

    def on(self, purpose: str, handler: Handler) -> None:
        self.handlers[purpose] = handler

    def calls_for(self, purpose: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["purpose"] == purpose]

    async def parse(
        self,
        *,
        purpose,
        instructions,
        input,
        schema,
        effort="medium",
        verbosity="medium",
        max_output_tokens=32_000,
        validate=None,
        usage=None,
        **_,
    ):
        text = input if isinstance(input, str) else _flatten(input)
        call = {"purpose": purpose, "instructions": instructions, "input": text, "schema": schema, "effort": effort}
        self.calls.append(call)
        if purpose not in self.handlers:
            raise AssertionError(f"FakeAI has no handler for {purpose!r}")
        result = self.handlers[purpose](call)
        candidates = result if isinstance(result, list) and result and not isinstance(result[0], dict) else [result]
        if usage is not None:
            usage.add(1000, 200)
        problems: list[str] = []
        # Like the real service: one first answer plus one repair attempt.
        for candidate in candidates[:2]:
            if isinstance(candidate, Exception):
                raise candidate
            problems = validate(candidate) if validate else []
            call.setdefault("problems", []).append(problems)
            if not problems:
                return candidate
        raise AIServiceError(
            "The AI's answer didn't pass ProfPilot's quality checks. Please try again.", code="ai_invalid_output"
        )

    async def parse_with_files(
        self, *, purpose, instructions, text, files, schema, effort="low", max_output_tokens=32_000, usage=None
    ):
        return await self.parse(purpose=purpose, instructions=instructions, input=text, schema=schema, effort=effort, usage=usage)

    async def embed(self, texts, *, usage=None):
        self.embedded_texts.extend(texts)
        if usage is not None:
            usage.add(sum(len(t) // 4 for t in texts), 0)
        return [embed_text(text) for text in texts]

    async def run_tool_loop(self, *, purpose, instructions, messages, tools, execute, effort="low", max_rounds=6, usage=None):
        self.calls.append({"purpose": purpose, "instructions": instructions, "messages": messages, "tools": tools})
        plan, reply = self.tool_scripts[purpose](messages)
        made: list[ToolCall] = []
        results = []
        for name, arguments in plan:
            made.append(ToolCall(call_id=f"call_{len(made)}", name=name, arguments=arguments))
            results.append(await execute(name, arguments))
        self.calls[-1]["tool_results"] = results
        return ToolLoopResult(text=reply, tool_calls=made)

    async def create_realtime_client_secret(self, *, session, ttl_seconds, safety_identifier):
        self.secrets.append({"session": session, "ttl": ttl_seconds, "safety_identifier": safety_identifier})
        return "ek_test_ephemeral", int(time.time()) + ttl_seconds


def _flatten(items: list[dict[str, Any]]) -> str:
    parts = []
    for item in items:
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(part.get("text", "") for part in content if isinstance(part, dict))
    return "\n\n".join(parts)


def new_usage() -> Usage:
    return Usage()
