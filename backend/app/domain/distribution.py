"""How an exam's marks are actually split, compared with what the ExamSpec asked for.

Percentages are always shares of the total points (marks), not of the number of
questions: a 10-point hard problem weighs more than a 2-point easy MCQ.
"""

from collections.abc import Iterable
from typing import Any

from app.schemas.ai import COGNITIVE_LEVELS, ExamSpec


def _get(question: Any, name: str) -> Any:
    return question.get(name) if isinstance(question, dict) else getattr(question, name, None)


def summarize(questions: Iterable[Any]) -> dict[str, Any]:
    items = list(questions)
    total = sum(float(_get(q, "points") or 0) for q in items)
    minutes = sum(float(_get(q, "estimated_minutes") or 0) for q in items)

    def share(predicate) -> float:
        if total <= 0:
            return 0.0
        return round(100 * sum(float(_get(q, "points") or 0) for q in items if predicate(q)) / total, 1)

    # Only when every question has a level: older questions don't record one.
    labelled = bool(items) and all(_get(q, "cognitive_level") for q in items)
    return {
        "questionCount": len(items),
        "totalPoints": round(total, 2),
        "estimatedMinutes": round(minutes, 1),
        "mcqPercent": share(lambda q: _get(q, "type") == "mcq"),
        "subjectivePercent": share(lambda q: _get(q, "type") != "mcq"),
        "easyPercent": share(lambda q: _get(q, "difficulty") == "easy"),
        "mediumPercent": share(lambda q: _get(q, "difficulty") == "medium"),
        "hardPercent": share(lambda q: _get(q, "difficulty") == "hard"),
        "bloom": (
            {level: share(lambda q, level=level: _get(q, "cognitive_level") == level) for level in COGNITIVE_LEVELS}
            if labelled
            else None
        ),
    }


def compare(summary: dict[str, Any], spec: ExamSpec | None, *, tolerance: float = 10.0) -> list[dict[str, Any]]:
    """Where the exam misses the spec by more than `tolerance` percentage points."""
    if spec is None or summary["questionCount"] == 0:
        return []
    warnings: list[dict[str, Any]] = []

    def check(dimension: str, label: str, actual: float, target: float) -> None:
        if abs(actual - target) > tolerance:
            warnings.append(
                {
                    "dimension": dimension,
                    "label": label,
                    "actual": actual,
                    "target": target,
                    "message": f"{label} questions are {actual:g}% of the marks; the plan asks for {target:g}%.",
                }
            )

    if spec.question_format:
        check("format", "Multiple-choice", summary["mcqPercent"], spec.question_format.mcq_percent)
    if spec.difficulty:
        check("difficulty", "Easy", summary["easyPercent"], spec.difficulty.easy_percent)
        check("difficulty", "Medium", summary["mediumPercent"], spec.difficulty.medium_percent)
        check("difficulty", "Hard", summary["hardPercent"], spec.difficulty.hard_percent)
    if spec.bloom and summary.get("bloom"):
        for level, target in spec.bloom.shares().items():
            check("bloom", f"{level.capitalize()}-level", summary["bloom"][level], target)
    if spec.duration_minutes and summary["estimatedMinutes"] > spec.duration_minutes * 1.1:
        warnings.append(
            {
                "dimension": "duration",
                "label": "Time",
                "actual": summary["estimatedMinutes"],
                "target": spec.duration_minutes,
                "message": (
                    f"The questions are estimated to take about {summary['estimatedMinutes']:g} minutes, "
                    f"longer than the {spec.duration_minutes}-minute duration."
                ),
            }
        )
    return warnings


def bloom_conflicts(spec: ExamSpec) -> list[str]:
    """Where the cognitive split can't be met with the other settings, for the professor to resolve.

    Multiple-choice questions can't assess creating something, so Create-level
    marks need written (subjective) questions. Nothing is changed silently.
    """
    if spec.bloom is None or spec.question_format is None:
        return []
    create, subjective = spec.bloom.create_percent, spec.question_format.subjective_percent
    if create <= subjective:
        return []
    return [
        f"{create}% of the marks are planned at the Create level, but only {subjective}% are for written "
        "(subjective) answers, and multiple-choice questions can't assess creating. Lower Create to at most "
        f"{subjective}% or raise the subjective share before approving."
    ]
