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

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.ai.openai_service import OpenAIService, Usage
from app.ai.prompts import DataFramer, reviewer_instructions
from app.db.supabase import Database
from app.domain import distribution
from app.schemas.ai import QuestionFix, ReviewIssue, ReviewOutput
from app.services.document_retrieval import DocumentRetrievalService, assign_refs, render_excerpts
from app.services.exam_generation import GenerationContext, load_generation_context
from app.services.exam_store import ExamStore, FullExam, question_problems, render_question, row_to_content, similar

logger = logging.getLogger("profpilot.review")


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
                output = await self._ai_review(ctx, full)
                issues.extend(await self._apply_ai_issues(full, output.issues))
                summary = output.summary
                minutes = output.estimated_total_minutes
            else:
                summary, minutes = "The exam has no questions yet.", None
        except Exception:
            await self.store.update_exam(exam_id, {"review_status": "failed"})
            raise

        full = await self.store.load(exam_id)
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
            summary = distribution.summarize(questions)
            for warning in distribution.compare(summary, spec):
                issues.append(
                    _issue(
                        "warning",
                        "difficulty"
                        if warning["dimension"] == "difficulty"
                        else ("duration" if warning["dimension"] == "duration" else "format"),
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
                    elif (twin["type"], twin["difficulty"], float(twin["points"])) != (
                        source["type"],
                        source["difficulty"],
                        float(source["points"]),
                    ):
                        issues.append(
                            _issue(
                                "warning",
                                "version_consistency",
                                f"Question {number} differs in type, difficulty or points between versions "
                                f"{primary['label']} and {version['label']}.",
                                twin,
                                number,
                                version["label"],
                                source="check",
                            )
                        )
        return issues

    # -- AI review ----------------------------------------------------------------------

    async def _ai_review(self, ctx: GenerationContext, full: FullExam) -> ReviewOutput:
        framer = DataFramer()
        concepts: list[str] = []
        for row in full.version_questions(full.primary_version["id"]):
            concepts.extend((row.get("concepts") or [])[:2])
        queries = list(dict.fromkeys(concepts))[:12] or [ctx.enhanced_prompt[:500]]
        excerpts = assign_refs(
            await DocumentRetrievalService(self.db, self.ai, self.usage).search(
                ctx.record.id, queries, document_names=ctx.document_names, per_query=3, max_excerpts=14, max_chars=24_000
            )
        )
        versions_text = []
        for version in full.versions:
            questions = full.version_questions(version["id"])
            body = "\n\n".join(render_question(row, number) for number, row in enumerate(questions, start=1))
            versions_text.append(framer.block("exam_version", f"Version {version['label']}", body))
        return await self.ai.parse(
            purpose="review_exam",
            instructions=reviewer_instructions(framer),
            input="\n\n".join(
                [
                    "APPROVED EXAM SPEC\n" + json.dumps(ctx.spec.model_dump(), indent=1),
                    "APPROVED BRIEF\n" + ctx.enhanced_prompt,
                    "THE EXAM TO REVIEW (with answer keys)\n" + "\n\n".join(versions_text),
                    render_excerpts(excerpts, framer, title="RELEVANT COURSE MATERIAL"),
                    "Review the exam now.",
                ]
            ),
            schema=ReviewOutput,
            effort="high",
            max_output_tokens=40_000,
            usage=self.usage,
        )

    async def _apply_ai_issues(self, full: FullExam, issues: list[ReviewIssue]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for issue in issues:
            label = issue.version_label or full.primary_version["label"]
            version = full.version_by_label(label) or full.primary_version
            questions = full.version_questions(version["id"])
            row = None
            if issue.question_number and 1 <= issue.question_number <= len(questions):
                row = questions[issue.question_number - 1]
            applied = False
            if row is not None and issue.fix is not None and row["status"] != "approved":
                try:
                    applied = await self._apply_fix(row, issue.fix, issue.message)
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

    async def _apply_fix(self, row: dict[str, Any], fix: QuestionFix, reason: str) -> bool:
        values: dict[str, Any] = {}
        choice_ids = [choice["id"] for choice in row.get("choices") or []]
        if fix.kind == "correct_answer_key":
            if row["type"] == "mcq":
                new_choice = (fix.correct_choice or "").strip().upper()
                if new_choice not in choice_ids or new_choice == row.get("correct_choice"):
                    return False
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
            if not fix.rubric or row.get("subparts"):
                return False
            if abs(sum(item.points for item in fix.rubric) - float(row["points"])) > 0.01:
                return False
            values["rubric"] = [item.model_dump() for item in fix.rubric]
        elif fix.kind == "clarify_wording":
            prompt = (fix.prompt or "").strip()
            old = row["prompt"]
            # Minimal rewording only: never a different question.
            if not prompt or prompt == old or similar(prompt, old) < 0.6 or not 0.6 <= len(prompt) / max(1, len(old)) <= 1.6:
                return False
            values["prompt"] = prompt
        if not values:
            return False
        await self.store.record_revision(row, source="ai_review_fix", instruction=f"Quality check: {reason}")
        await self.store.update_question(row["id"], values)
        return True


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
