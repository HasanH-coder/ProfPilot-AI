"""The AI quality check: an independent review of a generated exam.

Two layers:

- deterministic checks in code (structure, answer keys, splits by marks per
  version, time, duplicates, version equivalence);
- an independent review by the reasoning model, which solves every question
  itself and checks it against the spec and the course material.

The reviewer may repair only small, safe problems (a wrong MCQ key, a rubric
that doesn't add up, a wrong solution, a minimally reworded stem). Each repair
is validated, skipped for questions the professor approved (locked), and
recorded in the question's revision history. Anything bigger is reported to
the professor instead of changed.
"""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer, reviewer_instructions
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import QuestionFix, ReviewIssue, ReviewOutput
from app.services.document_retrieval import DocumentRetrievalService, assign_refs, render_excerpts
from app.services.exam_generation import GenerationContext, load_generation_context
from app.services.exam_store import (
    ExamStore,
    FullExam,
    integrity_leaks,
    leak_messages,
    leak_text,
    question_problems,
    question_texts,
    render_question,
    rewording_keeps_maths,
    row_to_content,
    similar,
)
from app.services.lecture_knowledge import professor_formula_text, traced_to_professor, unverified_refs
from app.services.slides import REVIEW_IMAGES, image_parts, question_slides, render_question_sources, slide_images

logger = logging.getLogger("profpilot.review")

# Mathematics in a question: LaTeX, or an equation between letters and numbers.
_MATHS = re.compile(r"\\\(|\\\[|\\frac|\\sum|[A-Za-z0-9)]\s*=\s*[-A-Za-z0-9(\\]")

# The review category of each distribution warning (see distribution.compare).
_DIMENSION_CATEGORIES = {"format": "format", "difficulty": "difficulty", "bloom": "cognitive_level", "duration": "duration"}


class ExamReviewService:
    def __init__(self, db: Database, ai: OpenAIService, usage: Usage) -> None:
        self.db = db
        self.ai = ai
        self.usage = usage
        self.store = ExamStore(db)

    async def review(self, exam_id: str, *, ctx: GenerationContext | None = None) -> dict[str, Any]:
        full = await self.store.load(exam_id)
        if ctx is None:
            ctx = await load_generation_context(self.db, full.exam["exam_project_id"], require_approved=False)
        await self.store.update_exam(exam_id, {"review_status": "running"})
        try:
            issues = self._deterministic_issues(full, ctx)
            if full.questions:
                # Leaks found in code go to the reviewer, which may remove one by a minimal rewording.
                found = [issue["message"] for issue in _leak_issues(full)]
                output = await self._ai_review(ctx, full, found)
                issues.extend(await self._apply_ai_issues(full, output.issues))
                summary = output.summary
                minutes = output.estimated_total_minutes
            else:
                summary, minutes = "The exam has no questions yet.", None
        except Exception:
            await self.store.update_exam(exam_id, {"review_status": "failed"})
            raise

        full = await self.store.load(exam_id)
        # Checked after the reviewer's fixes, so only leaks that remain are reported.
        issues.extend(_leak_issues(full))
        issues.extend(_pairing_notes(full))
        spec = full.spec
        unresolved = [issue for issue in issues if issue["severity"] in ("critical", "warning") and not issue["fixApplied"]]
        review = {
            "summary": summary,
            "checkedAt": datetime.now(UTC).isoformat(),
            "issues": [{"id": f"i{index + 1}", **issue} for index, issue in enumerate(issues)],
            "fixesApplied": sum(1 for issue in issues if issue["fixApplied"]),
            "estimatedTotalMinutes": minutes,
            "versions": [_version_summary(full, version, spec) for version in full.versions],
        }
        await self.store.update_exam(
            exam_id,
            {
                "review": review,
                "review_status": "needs_attention" if unresolved else "passed",
                "reviewed_at": review["checkedAt"],
            },
        )
        return review

    # -- Checks in code --------------------------------------------------------------

    def _deterministic_issues(self, full: FullExam, ctx: GenerationContext) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        spec = ctx.spec
        primary = full.primary_version
        primary_questions = full.version_questions(primary["id"])
        for version in full.versions:
            questions = full.version_questions(version["id"])
            for number, row in enumerate(questions, start=1):
                problems = question_problems(row_to_content(row), label=f"Question {number}")
                for problem in problems:
                    severity = "critical" if row["type"] == "mcq" and "correct_choice" in problem else "warning"
                    issues.append(
                        _issue(
                            severity,
                            "answer_key" if "answer" in problem or "choice" in problem else "other",
                            problem,
                            row,
                            number,
                            version["label"],
                            source="check",
                        )
                    )
                if row["needs_solution_review"]:
                    issues.append(
                        _issue(
                            "warning",
                            "solution",
                            "The question was edited; check that its answer key still fits.",
                            row,
                            number,
                            version["label"],
                            source="check",
                        )
                    )
                for other_number, other in enumerate(questions[: number - 1], start=1):
                    if similar(row["prompt"], other["prompt"]) > 0.85:
                        issues.append(
                            _issue(
                                "warning",
                                "duplication",
                                f"Question {number} is very similar to question {other_number}.",
                                row,
                                number,
                                version["label"],
                                source="check",
                            )
                        )
                        break
            if version["id"] == primary["id"]:
                issues.extend(self._unverified_formula_issues(full, questions, ctx))
            summary = distribution.summarize(questions)
            for warning in distribution.compare(summary, spec):
                issues.append(
                    _issue(
                        "warning",
                        _DIMENSION_CATEGORIES[warning["dimension"]],
                        f"Version {version['label']}: {warning['message']}",
                        None,
                        None,
                        version["label"],
                        source="check",
                    )
                )
            if version["id"] != primary["id"]:
                by_slot = {q["slot_id"]: q for q in questions}
                for number, source in enumerate(primary_questions, start=1):
                    twin = by_slot.get(source["slot_id"])
                    if twin is None:
                        issues.append(
                            _issue(
                                "critical",
                                "version_consistency",
                                f"Version {version['label']} has no equivalent of question {number}.",
                                source,
                                number,
                                primary["label"],
                                source="check",
                            )
                        )
                    elif (twin["type"], twin["difficulty"], twin.get("cognitive_level"), float(twin["points"])) != (
                        source["type"],
                        source["difficulty"],
                        source.get("cognitive_level"),
                        float(source["points"]),
                    ):
                        issues.append(
                            _issue(
                                "warning",
                                "version_consistency",
                                f"Question {number} differs in type, difficulty, Bloom's level or points between versions "
                                f"{primary['label']} and {version['label']}.",
                                twin,
                                number,
                                version["label"],
                                source="check",
                            )
                        )
        return issues

    def _unverified_formula_issues(
        self, full: FullExam, questions: list[dict[str, Any]], ctx: GenerationContext
    ) -> list[dict[str, Any]]:
        """Questions with mathematics, planned on lecture topics whose formulas weren't reliably
        extracted: the professor confirms them against the original pages before use. A question whose
        formulas are all ones the professor wrote in this assessment's instructions or notes is already
        confirmed by them."""
        plan = full.exam.get("plan") or {}
        refs_by_number = {slot.get("number"): slot.get("knowledge_refs") or [] for slot in plan.get("questions", [])}
        professor = professor_formula_text([ctx.record.setup.professor_prompt, ctx.record.setup.additional_notes])
        issues = []
        for number, row in enumerate(questions, start=1):
            topics = unverified_refs(refs_by_number.get(row.get("position"), []), ctx.documents, ctx.knowledge)
            content = row_to_content(row)
            if topics and _MATHS.search(" ".join(question_texts(content))) and not traced_to_professor(content, professor):
                issues.append(
                    _issue(
                        "warning",
                        "answer_key",
                        f"Question {number} uses mathematics from lecture material whose formulas weren't reliably "
                        f"extracted: {'; '.join(topics)}. Confirm its formulas and answer against the original "
                        "slides before using it. (Questions whose formulas all match ones you wrote in your "
                        "instructions or notes aren't flagged; calculations can't be matched that way.)",
                        row,
                        number,
                        full.primary_version["label"],
                        source="check",
                    )
                )
        return issues

    # -- AI review ----------------------------------------------------------------------

    async def _ai_review(self, ctx: GenerationContext, full: FullExam, found: list[str] | None = None) -> ReviewOutput:
        framer = DataFramer()
        concepts: list[str] = []
        for row in full.version_questions(full.primary_version["id"]):
            concepts.extend((row.get("concepts") or [])[:2])
        queries = list(dict.fromkeys(concepts))[:12] or [ctx.enhanced_prompt[:500]]
        # Each question's slides, as its writer saw them, to check its answer key against them.
        slides = ctx.slides
        cited = question_slides(full.version_questions(full.primary_version["id"]), slides)
        found_excerpts, images = await asyncio.gather(
            DocumentRetrievalService(self.db, self.ai, self.usage).search(
                ctx.record.id, queries, document_names=ctx.document_names, per_query=3, max_excerpts=14, max_chars=24_000
            ),
            slide_images(self.db, slides, [ref for _, refs in cited for ref in refs], limit=REVIEW_IMAGES, files=ctx.files),
        )
        excerpts = assign_refs(found_excerpts)
        versions_text = []
        for version in full.versions:
            questions = full.version_questions(version["id"])
            body = "\n\n".join(render_question(row, number) for number, row in enumerate(questions, start=1))
            versions_text.append(framer.block("exam_version", f"Version {version['label']}", body))
        return await self.ai.parse_with_files(
            purpose="review_exam",
            instructions=reviewer_instructions(framer),
            files=image_parts(images, slides),
            text="\n\n".join(
                part
                for part in [
                    "APPROVED EXAM SPEC\n" + json.dumps(ctx.spec.model_dump(), indent=1),
                    "APPROVED BRIEF\n" + ctx.enhanced_prompt,
                    "THE EXAM TO REVIEW (with answer keys)\n" + "\n\n".join(versions_text),
                    render_question_sources(cited, slides, framer),
                    render_excerpts(excerpts, framer, title="RELEVANT COURSE MATERIAL"),
                    (
                        "FOUND BY CHECKS IN CODE (reported to the professor already; don't report them again. For "
                        "each, propose 'clarify_wording' on the revealing question only if a minimal rewording "
                        "removes the give-away without changing what it tests)\n" + "\n".join(f"- {item}" for item in found)
                        if found
                        else ""
                    ),
                    "Review the exam now.",
                ]
                if part
            ),
            schema=ReviewOutput,
            effort="high",
            max_output_tokens=40_000,
            usage=self.usage,
        )

    async def _apply_ai_issues(self, full: FullExam, issues: list[ReviewIssue]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        updated: dict[str, dict[str, Any]] = {}  # questions already fixed in this review, as they are now
        for issue in issues:
            label = issue.version_label or full.primary_version["label"]
            version = full.version_by_label(label) or full.primary_version
            questions = [updated.get(q["id"], q) for q in full.version_questions(version["id"])]
            row = None
            if issue.question_number and 1 <= issue.question_number <= len(questions):
                row = questions[issue.question_number - 1]
            applied = False
            if row is not None and issue.fix is not None and row["status"] != "approved":
                try:
                    fixed = await self._apply_fix(row, issue.fix, issue.message, questions)
                    if fixed is not None:
                        updated[row["id"]], applied = fixed, True
                except Exception as error:  # a fix that fails validation is simply not applied
                    logger.info("Review fix not applied: %s", type(error).__name__)
            results.append(
                _issue(
                    issue.severity,
                    issue.category,
                    issue.message,
                    row,
                    issue.question_number,
                    label if row is not None or issue.version_label else None,
                    source="ai",
                    applied=applied,
                )
            )
        return results

    async def _apply_fix(
        self, row: dict[str, Any], fix: QuestionFix, reason: str, questions: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Applies a safe fix to this one question and returns it as fixed, or None when the fix isn't
        safe. Everything is checked before anything is saved: the question gains no problem, the whole
        version gains no give-away, and a rewording keeps the mathematics students read."""
        values = self._fix_values(row, fix)
        if not values:
            return None
        fixed = {**row, **values}
        content = row_to_content(fixed)
        if set(question_problems(content)) - set(question_problems(row_to_content(row))):
            return None
        numbers = list(range(1, len(questions) + 1))
        before = [row_to_content(q) for q in questions]
        after = [content if q["id"] == row["id"] else previous for q, previous in zip(questions, before, strict=True)]
        leaks_before, leaks_after = set(leak_messages(before, numbers)), set(leak_messages(after, numbers))
        if leaks_after - leaks_before:
            return None
        if fix.kind == "clarify_wording" and not rewording_keeps_maths(
            row_to_content(row), content, removes_leak=bool(leaks_before - leaks_after)
        ):
            return None
        # The revision and the change are saved together (None when the question changed meanwhile).
        return await self.store.change_question(row, values, source="ai_review_fix", instruction=f"Quality check: {reason}")

    @staticmethod
    def _fix_values(row: dict[str, Any], fix: QuestionFix) -> dict[str, Any] | None:
        values: dict[str, Any] = {}
        choice_ids = [choice["id"] for choice in row.get("choices") or []]
        parts: list[dict[str, Any]] = row.get("subparts") or []
        part = None
        if fix.subpart_label is not None:
            # Only wording and rubrics can be fixed part by part; the other fixes are for the whole question.
            label = fix.subpart_label.strip().strip("()").lower()
            part = next((index for index, p in enumerate(parts) if p["label"].strip().strip("()").lower() == label), None)
            if part is None or fix.kind not in ("clarify_wording", "fix_rubric"):
                return None
        if fix.kind == "correct_answer_key":
            if row["type"] == "mcq":
                new_choice = (fix.correct_choice or "").strip().upper()
                if new_choice not in choice_ids or new_choice == row.get("correct_choice"):
                    return None
                values["correct_choice"] = new_choice
            if fix.answer and fix.answer.strip():
                values["answer"] = fix.answer.strip()
            if fix.solution and fix.solution.strip():
                values["solution"] = fix.solution.strip()
        elif fix.kind == "fix_solution":
            if fix.answer and fix.answer.strip():
                values["answer"] = fix.answer.strip()
            if fix.solution and fix.solution.strip():
                values["solution"] = fix.solution.strip()
        elif fix.kind == "fix_rubric":
            # A rubric for the question, or for one of its parts, adding up to exactly its points. A question
            # with parts is marked by its parts' rubrics, so its own rubric isn't replaced.
            if part is not None:
                target = parts[part]
            else:
                target = None if parts else row
            if not fix.rubric or target is None:
                return None
            if abs(sum(item.points for item in fix.rubric) - float(target["points"])) > 0.01:
                return None
            rubric = [item.model_dump() for item in fix.rubric]
            if part is None:
                values["rubric"] = rubric
            else:
                values["subparts"] = [{**p, "rubric": rubric} if index == part else p for index, p in enumerate(parts)]
        elif fix.kind == "relabel_cognitive_level":
            # Only the label changes, to the level the question really assesses;
            # rewriting it to reach another level is left to the professor.
            level = fix.cognitive_level
            if not level or level == row.get("cognitive_level") or (row["type"] == "mcq" and level == "create"):
                return None
            values["cognitive_level"] = level
        elif fix.kind == "clarify_wording":
            # The question's prompt, or one part's: the part's answer, rubric and points stay as they are.
            prompt = (fix.prompt or "").strip()
            old = row["prompt"] if part is None else parts[part]["prompt"]
            # Minimal rewording only: never a different question.
            if not prompt or prompt == old or similar(prompt, old) < 0.6 or not 0.6 <= len(prompt) / max(1, len(old)) <= 1.6:
                return None
            if part is None:
                values["prompt"] = prompt
            else:
                values["subparts"] = [{**p, "prompt": prompt} if index == part else p for index, p in enumerate(parts)]
        return values or None


def _leak_issues(full: FullExam) -> list[dict[str, Any]]:
    """Every answer, result or explanation that one question of a version gives away to another."""
    issues = []
    for version in full.versions:
        rows = full.version_questions(version["id"])
        numbers = list(range(1, len(rows) + 1))
        for helped, revealing, kind, text in integrity_leaks([row_to_content(row) for row in rows]):
            issues.append(
                _issue(
                    "warning",
                    "answer_leakage",
                    f"{leak_text(kind, numbers[revealing], numbers[helped], text)}: remove it from question "
                    f"{numbers[revealing]} (e.g. name it, or use other values) or change question {numbers[helped]}.",
                    rows[revealing],
                    numbers[revealing],
                    version["label"],
                    source="check",
                )
            )
    return issues


# Bloom's level and difficulty pairs that are hard to realise well (the professor's choice is kept).
_DEMANDING_PAIRS = {
    ("remember", "medium"): "recalling closely related distinctions, several facts that must all be right, or "
    "precise terms, never trick wording or hidden application",
    ("remember", "hard"): "recalling closely related distinctions, several facts that must all be right, or "
    "precise terms, never trick wording or hidden application",
    ("understand", "hard"): "explaining an unfamiliar case or connecting several ideas in the student's own words, "
    "without the stem supplying the interpretation (intermediate values, conclusions or what to mention)",
}


def _pairing_notes(full: FullExam) -> list[dict[str, Any]]:
    """Suggestions (not problems) for questions whose Bloom's level and difficulty are a demanding pair."""
    notes = []
    for number, row in enumerate(full.version_questions(full.primary_version["id"]), start=1):
        advice = _DEMANDING_PAIRS.get((row.get("cognitive_level"), row["difficulty"]))
        if advice:
            notes.append(
                _issue(
                    "info",
                    "difficulty",
                    f"Question {number} is {row['difficulty']} at the {row['cognitive_level'].capitalize()} level, a "
                    f"demanding pair: its difficulty should come from {advice}. Check that it does; the setting itself "
                    "is kept as you chose it.",
                    row,
                    number,
                    full.primary_version["label"],
                    source="check",
                )
            )
    return notes


def _version_summary(full: FullExam, version: dict[str, Any], spec: Any) -> dict[str, Any]:
    summary = distribution.summarize(full.version_questions(version["id"]))
    return {"label": version["label"], "summary": summary, "warnings": distribution.compare(summary, spec)}


def _issue(
    severity: str,
    category: str,
    message: str,
    row: dict[str, Any] | None,
    number: int | None,
    version_label: str | None,
    *,
    source: str,
    applied: bool = False,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "message": message,
        "questionId": row["id"] if row else None,
        "questionNumber": number if row else None,
        "versionLabel": version_label,
        "source": source,
        "fixApplied": applied,
    }
