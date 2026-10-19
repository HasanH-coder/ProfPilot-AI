"""Reading, writing and validating exams, versions, sections and questions.

Questions are stored as structured rows (type, choices, answer key, rubric…),
never as one Markdown blob. Every write goes through the professor's own
Database connection, so Row Level Security and the table constraints (an MCQ's
correct answer must be one of its choices, etc.) apply on top of the checks here.
"""

import asyncio
import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import ExamSpec, QuestionContent
from app.schemas.common import parse_id
from app.services.document_parsers import EXTRACTION_GLYPHS

EXAM_COLUMNS = (
    "id, exam_project_id, mode, status, spec_snapshot, enhanced_prompt_snapshot, plan, review, "
    "review_status, reviewed_at, error_message, finalized_at, created_at, updated_at"
)
QUESTION_COLUMNS = (
    "id, exam_id, version_id, section_id, slot_id, position, type, difficulty, cognitive_level, points, prompt, "
    "choices, correct_choice, subparts, answer, solution, rubric, explanation, concepts, "
    "learning_objectives, source_refs, figure_document_id, estimated_minutes, status, "
    "needs_solution_review, created_at, updated_at"
)
# The fields a revision snapshot captures and a restore puts back.
SNAPSHOT_FIELDS = (
    "type",
    "difficulty",
    "cognitive_level",
    "points",
    "prompt",
    "choices",
    "correct_choice",
    "subparts",
    "answer",
    "solution",
    "rubric",
    "explanation",
    "concepts",
    "learning_objectives",
    "source_refs",
    "figure_document_id",
    "estimated_minutes",
    "section_id",
    "needs_solution_review",
)
VERSION_LABELS = "ABCDEFGHIJKLMNOPQRST"
_POINTS_TOLERANCE = 0.01


@dataclass
class FullExam:
    exam: dict[str, Any]
    versions: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    revision_counts: dict[str, int] = field(default_factory=dict)

    @property
    def spec(self) -> ExamSpec | None:
        snapshot = (self.exam.get("spec_snapshot") or {}).get("spec")
        return ExamSpec.model_validate(snapshot) if snapshot else None

    def version_questions(self, version_id: str) -> list[dict[str, Any]]:
        return sorted((q for q in self.questions if q["version_id"] == version_id), key=lambda q: q["position"])

    def version_by_label(self, label: str) -> dict[str, Any] | None:
        return next((v for v in self.versions if v["label"] == label), None)

    @property
    def primary_version(self) -> dict[str, Any]:
        return self.versions[0]


# =============================================================================
# Validation
# =============================================================================


def normalize_content(content: QuestionContent) -> QuestionContent:
    """Tidies harmless formatting differences before validation."""
    content = content.model_copy(deep=True)
    content.prompt = content.prompt.strip()
    content.answer = content.answer.strip()
    content.solution = content.solution.strip()
    if content.type == "mcq":
        if content.correct_choice:
            content.correct_choice = content.correct_choice.strip().upper()
        content.subparts = None
    else:
        content.choices = None
        content.correct_choice = None
    if content.subparts == []:
        content.subparts = None
    if content.rubric == []:
        content.rubric = None
    return content


def question_texts(content: QuestionContent) -> list[str]:
    """Every piece of text a student or marker reads."""
    texts = [content.prompt, content.answer, content.solution, content.explanation or ""]
    texts += [choice.text for choice in content.choices or []]
    texts += [item.criterion for item in content.rubric or []]
    for part in content.subparts or []:
        texts += [part.prompt, part.answer, *(item.criterion for item in part.rubric)]
    return [text for text in texts if text]


def student_texts(content: QuestionContent) -> list[str]:
    """What a student reads: the prompt, the choices and the subpart prompts (never the key)."""
    texts = [content.prompt, *(choice.text for choice in content.choices or [])]
    texts += [part.prompt for part in content.subparts or []]
    return [text for text in texts if text]


def question_problems(content: QuestionContent, *, label: str = "The question", manual: bool = False) -> list[str]:
    """Business rules a question must satisfy before it is saved.

    AI-written questions must be complete (4 choices, a full answer key and
    rubric). A professor's manual edit only has to be structurally valid: an
    incomplete answer key is flagged for review instead of rejected.
    """
    problems: list[str] = []
    if not content.prompt:
        problems.append(f"{label} has no question text.")
    if not manual and any(EXTRACTION_GLYPHS.search(text) for text in question_texts(content)):
        problems.append(
            f"{label} contains characters copied from a PDF's extracted text (such as '𝒇' or '𝒆'), where "
            "mathematics may be garbled; write all mathematics in LaTeX and never copy extracted formulas."
        )
    if not manual and any(_INTERNAL_ID.search(text) for text in question_texts(content)):
        problems.append(
            f"{label} writes internal ids (excerpt ids such as 'S4' or topic ids such as 'L1.t3') in its text. "
            "ProfPilot attaches the sources with their pages itself: keep excerpt ids in source_excerpt_ids "
            "and cite pages only where the text needs them ('page 38')."
        )
    if not manual and any(_INTERNAL_ID.search(text) for text in [*content.concepts, *content.learning_objectives]):
        problems.append(
            f"{label}'s concepts or learning objectives contain internal ids or sources: list short concept names "
            "(e.g. 'Sigmoid function') and objectives only."
        )
    if not manual and any(_MARKS_NOTE.search(text) for text in student_texts(content)):
        problems.append(
            f"{label} writes marks into its text (e.g. '(2 marks)'). ProfPilot shows the marks of each question "
            "and part itself: remove them from the prompt and subpart prompts."
        )
    if not content.answer and not manual:
        problems.append(f"{label} has no answer.")
    if content.type == "mcq":
        choices = content.choices or []
        ids = [choice.id for choice in choices]
        low, high = (2, 8) if manual else (3, 6)
        if not low <= len(choices) <= high:
            problems.append(f"{label} is multiple choice and needs {'2 to 8' if manual else '3 to 6 (normally 4)'} choices.")
        if len(set(ids)) != len(ids):
            problems.append(f"{label} has duplicate choice ids.")
        texts = [re.sub(r"\s+", " ", choice.text.strip().lower()) for choice in choices]
        if any(not text for text in texts) or len(set(texts)) != len(texts):
            problems.append(f"{label} has empty or repeated choices.")
        if content.correct_choice not in ids:
            problems.append(f"{label}'s correct_choice must be one of its choice ids ({', '.join(ids)}).")
        if content.cognitive_level == "create" and not manual:
            problems.append(f"{label} is multiple choice, which can't assess the Create level; use another level.")
    else:
        if content.choices or content.correct_choice:
            problems.append(f"{label} is not multiple choice, so it must not have choices.")
        if not content.solution and not manual:
            problems.append(f"{label} needs a worked solution or model answer.")
        if content.subparts:
            labels = [part.label.strip().lower() for part in content.subparts]
            if len(set(labels)) != len(labels):
                problems.append(f"{label} has repeated subpart labels.")
            total = sum(part.points for part in content.subparts)
            if abs(total - content.points) > _POINTS_TOLERANCE:
                problems.append(f"{label}'s subpart points add up to {total:g}, but the question is worth {content.points:g}.")
            repeated = [part.label for part in content.subparts if _restates(content.prompt, part)]
            if repeated and not manual:
                problems.append(
                    f"{label}'s main prompt repeats its subpart task(s) ({', '.join(repeated)}), and students see both. "
                    "Keep only what the subparts share (scenario, data, definitions) in the prompt; each task "
                    "belongs once, in its subpart."
                )
            for part in content.subparts:
                if not part.prompt.strip() or (not part.answer.strip() and not manual):
                    problems.append(f"{label} part ({part.label}) needs a prompt and an answer.")
                if not manual and part.rubric and abs(sum(item.points for item in part.rubric) - part.points) > _POINTS_TOLERANCE:
                    problems.append(f"{label} part ({part.label})'s rubric must add up to {part.points:g} points.")
        elif not content.rubric and not manual:
            problems.append(f"{label} needs a marking rubric.")
        if content.rubric and not content.subparts and not manual:
            total = sum(item.points for item in content.rubric)
            if abs(total - content.points) > _POINTS_TOLERANCE:
                problems.append(f"{label}'s rubric adds up to {total:g} points, but the question is worth {content.points:g}.")
    return problems


# An internal id written into question text: a lecture topic id ("L1.t3"), or an excerpt id ("S4")
# cited as a source ("Source: …; S4", "excerpt S2"). A bare "S1" (e.g. a state name) is not matched.
_INTERNAL_ID = re.compile(r"\bL\d+\.t\d+\b|(?i:\b(?:sources?|excerpts?|refs?|references?)\b)[^\n]{0,80}?\bS\d{1,3}\b")


# Marks written into question text ("(2 marks)", "[3 pts]"): ProfPilot shows them itself.
_MARKS_NOTE = re.compile(r"(?i)[(\[]\s*\d+(?:\.\d+)?\s*(?:marks?|points?|pts?)\s*[)\]]")

# -- Answer leakage between questions -------------------------------------------------
#
# A multiple-choice question is compromised when another question's student-facing text
# states its correct answer: verbatim, as the same mathematics written differently, or as a
# close paraphrase. Shared vocabulary, formulas other questions legitimately need, and text
# that matches the distractors as well as the key are not leaks.

_MIN_KEY = 6  # shorter keys ('0.5', 'Class 1') match by chance
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "the a an and or of to in on for by with as at from is are be been being it its this that these those which "
    "who whom than then into over under each every any all some such not no can may must should would will".split()
)


def canonical_maths(text: str) -> str:
    """One spelling for the same mathematics, so LaTeX and plain forms compare equal:
    '\\frac{1}{1+e^{-z}}', '\\(\\dfrac{1}{1 + \\exp(-z)}\\)' and '1/(1 + e^(−z))' all give '1/1+e^-z'."""
    text = text.replace("−", "-").replace("·", "*").replace("×", "*").replace("\\exp", "e^")
    text = re.sub(r"\\[()\[\]]|\$", "", text)
    text = re.sub(r"\\[dt]frac", r"\\frac", text)
    text = re.sub(r"\\frac\s*", "", text).replace("}{", "}/{")
    text = re.sub(r"\\(?:cdot|times)", "*", text)
    text = re.sub(r"\\(?:left|right|displaystyle|[,;:! ]|q?quad)", "", text)
    return re.sub(r"[\s{}()\[\]]", "", text).lower()


def _keys(text: str) -> list[str]:
    """The forms of an answer to look for: all of it, and the right side of an equation."""
    whole = canonical_maths(text).rstrip(".")
    keys = [whole] + ([whole.rsplit("=", 1)[1]] if "=" in whole else [])
    return [key for key in dict.fromkeys(keys) if len(key) >= _MIN_KEY]


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) > 2 and word not in _STOPWORDS}


def _covered(words: set[str], sentence: str) -> float:
    return len(words & _words(sentence)) / len(words) if words else 0.0


def answer_leaks(questions: list[QuestionContent]) -> list[tuple[int, int, str]]:
    """(answer index, revealing index, the revealed answer) for every multiple-choice answer that
    another question's student-facing text gives away. Indexes are positions in `questions`."""
    shown = [
        (canonical_maths(" ".join(student_texts(q))), re.split(r"(?<=[.!?])\s+|\n+", " \n".join(student_texts(q))))
        for q in questions
    ]
    leaks = []
    for index, question in enumerate(questions):
        if question.type != "mcq" or not question.choices:
            continue
        key = next((choice for choice in question.choices if choice.id == question.correct_choice), None)
        if key is None:
            continue
        distractors = [choice.text for choice in question.choices if choice is not key]
        key_forms, key_words = _keys(key.text), _words(key.text)
        for other, (maths, sentences) in enumerate(shown):
            if other == index:
                continue
            # The same mathematics or wording, and not a distractor's: then it gives the answer away.
            revealed = any(form in maths for form in key_forms) and not any(
                form in maths for text in distractors for form in _keys(text)
            )
            # A paraphrase: one sentence covers most of the answer's words, and most of the words only the
            # answer has (options that reuse the same words in another order have none, so never match).
            distinctive = key_words - set().union(*(_words(text) for text in distractors))
            if not revealed and len(key_words) >= 5 and len(distinctive) >= 2:
                revealed = any(
                    _covered(key_words, sentence) >= 0.75 and _covered(distinctive, sentence) >= 0.67 for sentence in sentences
                )
            if revealed:
                leaks.append((index, other, key.text))
    return leaks


def leak_messages(questions: list[QuestionContent], numbers: list[int], *, only: set[int] | None = None) -> list[str]:
    """Problems for the leaks among these questions (numbered `numbers`); with `only`, just the
    leaks involving those question numbers."""
    messages = []
    for answer, revealing, text in answer_leaks(questions):
        if only is not None and numbers[answer] not in only and numbers[revealing] not in only:
            continue
        messages.append(
            f"Question {numbers[revealing]}'s text gives away the answer to question {numbers[answer]} "
            f'("{text[:120]}"). Don\'t state what another question asks students to recall or identify: refer '
            "to it by name (e.g. 'the sigmoid function') or let students use it from memory."
        )
    return messages


def _restates(prompt: str, part: Any) -> bool:
    """Whether the main prompt also states this subpart: a line opening with its label ('(a) …'),
    or the subpart's own task text."""
    label = re.escape(part.label.strip().strip("()").lower())
    if re.search(rf"(?im)^\s*\(?{label}\)\s+\S", prompt):
        return True
    task = re.sub(r"\s+", " ", part.prompt).strip().lower()
    return len(task) >= 40 and task[:80] in re.sub(r"\s+", " ", prompt).lower()


def similar(a: str, b: str) -> float:
    """How alike two question texts are (0–1), ignoring case and spacing."""

    def clean(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower()).strip()[:400]

    return difflib.SequenceMatcher(None, clean(a), clean(b)).ratio()


# =============================================================================
# Conversion
# =============================================================================


def content_to_row(
    content: QuestionContent,
    *,
    excerpt_refs: dict[str, dict[str, Any]] | None = None,
    allowed_figures: set[str] | None = None,
    topic_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`topic_refs`: the planned lecture topics' exact pages (see lecture_knowledge.topic_sources)."""
    refs = list(topic_refs or [])
    for ref in content.source_excerpt_ids:
        if excerpt_refs and ref in excerpt_refs:
            refs.append(excerpt_refs[ref])
    figure = content.figure_document_id if allowed_figures and content.figure_document_id in allowed_figures else None
    return {
        "type": content.type,
        "difficulty": content.difficulty,
        "cognitive_level": content.cognitive_level,
        "points": content.points,
        "prompt": content.prompt,
        "choices": [choice.model_dump() for choice in content.choices] if content.choices else None,
        "correct_choice": content.correct_choice if content.type == "mcq" else None,
        "subparts": [part.model_dump() for part in content.subparts] if content.subparts else None,
        "answer": content.answer,
        "solution": content.solution or None,
        "rubric": [item.model_dump() for item in content.rubric] if content.rubric else None,
        "explanation": content.explanation or None,
        "concepts": [concept[:200] for concept in content.concepts[:12]],
        "learning_objectives": [objective[:300] for objective in content.learning_objectives[:6]],
        "source_refs": refs,
        "figure_document_id": figure,
        "estimated_minutes": round(content.estimated_minutes, 1),
        "needs_solution_review": False,
    }


def row_to_content(row: dict[str, Any]) -> QuestionContent:
    return QuestionContent.model_validate(
        {
            "type": row["type"],
            "difficulty": row["difficulty"],
            "cognitive_level": row.get("cognitive_level"),
            "points": float(row["points"]),
            "prompt": row["prompt"],
            "choices": row.get("choices"),
            "correct_choice": row.get("correct_choice"),
            "subparts": row.get("subparts"),
            "answer": row.get("answer") or "",
            "solution": row.get("solution") or "",
            "rubric": row.get("rubric"),
            "explanation": row.get("explanation"),
            "concepts": row.get("concepts") or [],
            "learning_objectives": row.get("learning_objectives") or [],
            "source_excerpt_ids": [],
            "estimated_minutes": float(row.get("estimated_minutes") or 1),
            "figure_document_id": row.get("figure_document_id"),
        }
    )


def render_question(row: dict[str, Any], number: int, *, include_key: bool = True) -> str:
    """A question as plain text, for prompts."""
    level = f", Bloom's level: {row['cognitive_level']}" if row.get("cognitive_level") else ""
    lines = [f"Q{number} [{row['type']}, {row['difficulty']}{level}, {float(row['points']):g} pts]", row["prompt"]]
    for choice in row.get("choices") or []:
        lines.append(f"  {choice['id']}. {choice['text']}")
    for part in row.get("subparts") or []:
        lines.append(f"  ({part['label']}) [{float(part['points']):g} pts] {part['prompt']}")
        if include_key:
            lines.append(f"     Answer: {part.get('answer', '')}")
            if part.get("rubric"):
                lines.append(f"     Rubric: {_rubric_text(part['rubric'])}")
    if include_key:
        if row.get("correct_choice"):
            lines.append(f"Correct choice: {row['correct_choice']}")
        lines.append(f"Answer: {row.get('answer') or ''}")
        if row.get("solution"):
            lines.append(f"Solution: {row['solution']}")
        if row.get("rubric"):
            lines.append(f"Rubric: {_rubric_text(row['rubric'])}")
        if sources := source_labels(row):
            lines.append("Sources (attached by ProfPilot): " + "; ".join(sources))
    return "\n".join(lines)


def _rubric_text(rubric: list[dict[str, Any]]) -> str:
    return "; ".join(f"{item['criterion']} ({float(item['points']):g})" for item in rubric)


def source_labels(row: dict[str, Any]) -> list[str]:
    """A question's sources for the answer key: the exact pages of the lecture topics it was planned
    on, then the retrieved excerpts (whose labels span their chunk's pages) of any other file."""
    refs = row.get("source_refs") or []
    exact = [ref for ref in refs if ref.get("topic") and ref.get("label")]
    covered = {ref.get("document_id") for ref in exact}
    others = [ref for ref in refs if not ref.get("topic") and ref.get("label") and ref.get("document_id") not in covered]
    return list(dict.fromkeys(ref["label"] for ref in [*exact, *others]))


def serialize_question(row: dict[str, Any], number: int, revision_count: int = 0) -> dict[str, Any]:
    return {
        "id": row["id"],
        "examId": row["exam_id"],
        "versionId": row["version_id"],
        "sectionId": row.get("section_id"),
        "slotId": row["slot_id"],
        "number": number,
        "position": row["position"],
        "type": row["type"],
        "difficulty": row["difficulty"],
        "cognitiveLevel": row.get("cognitive_level"),
        "points": float(row["points"]),
        "prompt": row["prompt"],
        "choices": row.get("choices"),
        "correctChoice": row.get("correct_choice"),
        "subparts": row.get("subparts"),
        "answer": row.get("answer"),
        "solution": row.get("solution"),
        "rubric": row.get("rubric"),
        "explanation": row.get("explanation"),
        "concepts": row.get("concepts") or [],
        "learningObjectives": row.get("learning_objectives") or [],
        "sources": source_labels(row),
        "figureDocumentId": row.get("figure_document_id"),
        "estimatedMinutes": float(row["estimated_minutes"]) if row.get("estimated_minutes") is not None else None,
        "status": row["status"],
        "needsSolutionReview": row["needs_solution_review"],
        "revisionCount": revision_count,
        "updatedAt": row["updated_at"],
    }


def serialize_exam(full: FullExam) -> dict[str, Any]:
    exam = full.exam
    spec = full.spec
    numbered: list[dict[str, Any]] = []
    versions = []
    for version in full.versions:
        questions = full.version_questions(version["id"])
        for number, row in enumerate(questions, start=1):
            numbered.append(serialize_question(row, number, full.revision_counts.get(row["id"], 0)))
        summary = distribution.summarize(questions)
        versions.append(
            {
                "id": version["id"],
                "label": version["label"],
                "position": version["position"],
                "summary": summary,
                "warnings": distribution.compare(summary, spec),
            }
        )
    plan = exam.get("plan") or {}
    return {
        "id": exam["id"],
        "assessmentId": exam["exam_project_id"],
        "mode": exam["mode"],
        "status": exam["status"],
        "errorMessage": exam.get("error_message"),
        "reviewStatus": exam["review_status"],
        "review": exam.get("review"),
        "reviewedAt": exam.get("reviewed_at"),
        "finalizedAt": exam.get("finalized_at"),
        "createdAt": exam["created_at"],
        "updatedAt": exam["updated_at"],
        "spec": spec.model_dump() if spec else None,
        "plannedQuestionCount": len(plan.get("questions") or []),
        "versions": versions,
        "sections": [
            {"id": s["id"], "position": s["position"], "title": s["title"], "instructions": s.get("instructions")}
            for s in sorted(full.sections, key=lambda s: s["position"])
        ],
        "questions": numbered,
    }


# =============================================================================
# Store
# =============================================================================


class ExamStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _owned(self, *filters: tuple[str, str, Any]) -> list[tuple[str, str, Any]]:
        return [*filters, ("professor_id", "eq", self.db.professor_id)]

    async def exam_for_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        return await self.db.select_one(
            "exams", columns=EXAM_COLUMNS, filters=self._owned(("exam_project_id", "eq", assessment_id))
        )

    async def get_exam(self, exam_id: str) -> dict[str, Any]:
        exam_id = parse_id(exam_id, what="exam")
        row = await self.db.select_one("exams", columns=EXAM_COLUMNS, filters=self._owned(("id", "eq", exam_id)))
        if row is None:
            raise NotFoundError("This exam doesn't exist, or you don't have access to it.")
        return row

    async def load(self, exam_id: str) -> FullExam:
        exam = await self.get_exam(exam_id)
        # Independent reads of the exam's parts, at once.
        versions, sections, questions = await asyncio.gather(
            self.db.select(
                "exam_versions",
                columns="id, label, position",
                filters=self._owned(("exam_id", "eq", exam["id"])),
                order=[("position", "asc")],
            ),
            self.db.select(
                "exam_sections",
                columns="id, position, title, instructions",
                filters=self._owned(("exam_id", "eq", exam["id"])),
                order=[("position", "asc")],
            ),
            self.db.select(
                "exam_questions",
                columns=QUESTION_COLUMNS,
                filters=self._owned(("exam_id", "eq", exam["id"])),
                order=[("position", "asc")],
            ),
        )
        revisions = (
            await self.db.select(
                "question_revisions",
                columns="question_id",
                filters=self._owned(
                    ("question_id", "in", [q["id"] for q in questions] or ["00000000-0000-0000-0000-000000000000"])
                ),
            )
            if questions
            else []
        )
        counts: dict[str, int] = {}
        for revision in revisions:
            counts[revision["question_id"]] = counts.get(revision["question_id"], 0) + 1
        return FullExam(exam=exam, versions=versions, sections=sections, questions=questions, revision_counts=counts)

    async def create_exam(
        self, assessment_id: str, *, mode: str, spec_envelope: dict[str, Any], enhanced_prompt: str | None, status: str
    ) -> dict[str, Any]:
        rows = await self.db.insert(
            "exams",
            {
                "exam_project_id": assessment_id,
                "mode": mode,
                "status": status,
                "spec_snapshot": spec_envelope,
                "enhanced_prompt_snapshot": enhanced_prompt,
            },
            columns=EXAM_COLUMNS,
        )
        return rows[0]

    async def update_exam(self, exam_id: str, values: dict[str, Any]) -> None:
        await self.db.update("exams", values, filters=self._owned(("id", "eq", exam_id)), columns="id")

    async def delete_exam(self, exam_id: str) -> None:
        await self.db.delete("exams", filters=self._owned(("id", "eq", exam_id)))

    async def create_versions(self, exam_id: str, count: int) -> list[dict[str, Any]]:
        if not 1 <= count <= len(VERSION_LABELS):
            raise InvalidInputError("An exam can have 1 to 20 versions.")
        return await self.db.insert(
            "exam_versions",
            [{"exam_id": exam_id, "label": VERSION_LABELS[i], "position": i + 1} for i in range(count)],
            columns="id, label, position",
        )

    async def create_sections(self, exam_id: str, sections: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
        if not sections:
            sections = [("Questions", None)]
        return await self.db.insert(
            "exam_sections",
            [
                {"exam_id": exam_id, "position": i + 1, "title": title[:200] or f"Section {i + 1}", "instructions": instructions}
                for i, (title, instructions) in enumerate(sections)
            ],
            columns="id, position, title, instructions",
        )

    async def insert_question(self, exam_id: str, version_id: str, values: dict[str, Any]) -> dict[str, Any]:
        rows = await self.db.insert(
            "exam_questions", {"exam_id": exam_id, "version_id": version_id, **values}, columns=QUESTION_COLUMNS
        )
        return rows[0]

    async def get_question(self, question_id: str) -> dict[str, Any]:
        question_id = parse_id(question_id, what="question")
        row = await self.db.select_one("exam_questions", columns=QUESTION_COLUMNS, filters=self._owned(("id", "eq", question_id)))
        if row is None:
            raise NotFoundError("This question doesn't exist, or you don't have access to it.")
        return row

    async def update_question(self, question_id: str, values: dict[str, Any]) -> dict[str, Any]:
        rows = await self.db.update(
            "exam_questions", values, filters=self._owned(("id", "eq", question_id)), columns=QUESTION_COLUMNS
        )
        if not rows:
            raise NotFoundError("This question doesn't exist, or you don't have access to it.")
        return rows[0]

    async def delete_question(self, question_id: str) -> None:
        await self.db.delete("exam_questions", filters=self._owned(("id", "eq", question_id)))

    async def question_number(self, question: dict[str, Any]) -> int:
        """The question's number in its version (numbering follows the order), without loading the exam."""
        rows = await self.db.select(
            "exam_questions",
            columns="id, position",
            filters=self._owned(("version_id", "eq", question["version_id"])),
            order=[("position", "asc")],
        )
        return next((number for number, row in enumerate(rows, start=1) if row["id"] == question["id"]), len(rows))

    async def next_position(self, version_id: str) -> int:
        rows = await self.db.select(
            "exam_questions",
            columns="position",
            filters=self._owned(("version_id", "eq", version_id)),
            order=[("position", "desc")],
            limit=1,
        )
        return (rows[0]["position"] + 1) if rows else 1

    async def record_revision(self, question: dict[str, Any], *, source: str, instruction: str | None) -> dict[str, Any]:
        """Saves the question as it is now, before a change, so it can be restored."""
        latest = await self.db.select(
            "question_revisions",
            columns="revision_number",
            filters=self._owned(("question_id", "eq", question["id"])),
            order=[("revision_number", "desc")],
            limit=1,
        )
        number = (latest[0]["revision_number"] + 1) if latest else 1
        snapshot = {name: question.get(name) for name in SNAPSHOT_FIELDS}
        rows = await self.db.insert(
            "question_revisions",
            {
                "question_id": question["id"],
                "revision_number": number,
                "source": source,
                "instruction": (instruction or "")[:2000] or None,
                "snapshot": snapshot,
            },
            columns="id, revision_number, source, instruction, created_at",
        )
        return rows[0]

    async def revisions(self, question_id: str) -> list[dict[str, Any]]:
        return await self.db.select(
            "question_revisions",
            columns="id, revision_number, source, instruction, snapshot, created_at",
            filters=self._owned(("question_id", "eq", question_id)),
            order=[("revision_number", "desc")],
        )

    async def require_editable(self, question: dict[str, Any]) -> dict[str, Any]:
        """The exam the question belongs to, if it can be changed right now."""
        exam = await self.get_exam(question["exam_id"])
        if exam["status"] == "generating":
            raise ConflictError("Wait until the exam has finished generating.", code="exam_generating")
        return exam
