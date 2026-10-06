"""Professor personalization for the Assessment Agent (first version).

ProfPilot learns only from work the professor accepted:

- an exam they marked as final (its splits, duration, question types, sections);
- an AI revision they kept and approved (e.g. "more application-based").

Drafts, regenerated plans and rejected or undone suggestions are never learned
from. The learned profile is computed in code from these signals (no AI call),
so it is predictable and the professor can read, disable or reset it. Prompts
present it as gentle guidance; the current assessment's settings always win.
"""

from collections import Counter
from statistics import mean, median
from typing import Any

from app.db.supabase import Database
from app.domain import distribution

_PRESET_TENDENCIES = {
    "harder": "Often makes questions harder than first proposed",
    "easier": "Often makes questions easier than first proposed",
    "application": "Prefers application-based questions over recall",
    "clearer": "Values very clear, unambiguous wording",
    "shorter": "Prefers concise questions with little reading",
    "alternative": "Often asks for alternative questions on the same objective",
    "replace": "Often replaces proposed questions with different topics",
}
_MAX_EXAMS = 20
_MAX_REVISIONS = 200


class PreferenceService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _own(self) -> list[tuple[str, str, Any]]:
        return [("professor_id", "eq", self.db.professor_id)]

    async def get(self) -> dict[str, Any]:
        row = await self.db.select_one(
            "professor_preferences", columns="explicit_notes, learned, learning_enabled, updated_at", filters=self._own()
        )
        return {
            "explicitNotes": (row or {}).get("explicit_notes") or "",
            "learningEnabled": bool((row or {}).get("learning_enabled", True)),
            "learned": (row or {}).get("learned") or {},
            "updatedAt": (row or {}).get("updated_at"),
        }

    async def update(self, *, explicit_notes: str | None = None, learning_enabled: bool | None = None) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if explicit_notes is not None:
            values["explicit_notes"] = explicit_notes.strip()[:2000] or None
        if learning_enabled is not None:
            values["learning_enabled"] = learning_enabled
        await self._upsert(values)
        return await self.get()

    async def reset_learned(self) -> dict[str, Any]:
        await self.db.delete("preference_signals", filters=self._own())
        await self._upsert({"learned": {}})
        return await self.get()

    async def _learning_enabled(self) -> bool:
        row = await self.db.select_one("professor_preferences", columns="learning_enabled", filters=self._own())
        return bool((row or {}).get("learning_enabled", True))

    # -- Signals -------------------------------------------------------------------------

    async def record_exam_finalized(
        self, assessment_id: str, questions: list[dict[str, Any]], spec: Any, versions: int, section_titles: list[str]
    ) -> None:
        if not await self._learning_enabled() or not questions:
            return
        summary = distribution.summarize(questions)
        payload = {
            "versions": versions,
            "questionCount": summary["questionCount"],
            "totalPoints": summary["totalPoints"],
            "mcqPercent": summary["mcqPercent"],
            "easyPercent": summary["easyPercent"],
            "mediumPercent": summary["mediumPercent"],
            "hardPercent": summary["hardPercent"],
            "estimatedMinutes": summary["estimatedMinutes"],
            "durationMinutes": getattr(spec, "duration_minutes", None),
            "typeCounts": dict(Counter(q["type"] for q in questions)),
            "sectionTitles": section_titles[:10],
            "averagePromptCharacters": round(mean(len(q["prompt"]) for q in questions)),
        }
        # One signal per assessment: finalizing again replaces the earlier one.
        await self.db.delete(
            "preference_signals",
            filters=[*self._own(), ("exam_project_id", "eq", assessment_id), ("kind", "eq", "exam_finalized")],
        )
        await self.db.insert(
            "preference_signals", {"exam_project_id": assessment_id, "kind": "exam_finalized", "payload": payload}, columns="id"
        )
        await self.recompute()

    async def record_accepted_revision(self, question: dict[str, Any]) -> None:
        """Called when the professor approves a question; counts only if its last change was an AI revision."""
        if not await self._learning_enabled():
            return
        latest = await self.db.select(
            "question_revisions",
            columns="id, source, instruction",
            filters=[*self._own(), ("question_id", "eq", question["id"])],
            order=[("revision_number", "desc")],
            limit=1,
        )
        if not latest or latest[0]["source"] != "ai_revision":
            return
        instruction = latest[0].get("instruction") or ""
        preset = instruction[1:].split("]", 1)[0] if instruction.startswith("[") else None
        already = await self.db.select(
            "preference_signals",
            columns="id",
            filters=[*self._own(), ("kind", "eq", "revision_accepted"), ("payload", "cs", {"revisionId": latest[0]["id"]})],
            limit=1,
        )
        if already:
            return
        exam = await self.db.select_one(
            "exams", columns="exam_project_id", filters=[*self._own(), ("id", "eq", question["exam_id"])]
        )
        await self.db.insert(
            "preference_signals",
            {
                "exam_project_id": (exam or {}).get("exam_project_id"),
                "kind": "revision_accepted",
                "payload": {
                    "revisionId": latest[0]["id"],
                    "preset": preset if preset in _PRESET_TENDENCIES else None,
                    "instruction": None if preset else instruction[:300],
                    "questionType": question["type"],
                    "difficulty": question["difficulty"],
                },
            },
            columns="id",
        )
        await self.recompute()

    # -- Learned profile ---------------------------------------------------------------------

    async def recompute(self) -> dict[str, Any]:
        exams = await self.db.select(
            "preference_signals",
            columns="payload",
            filters=[*self._own(), ("kind", "eq", "exam_finalized")],
            order=[("created_at", "desc")],
            limit=_MAX_EXAMS,
        )
        revisions = await self.db.select(
            "preference_signals",
            columns="payload",
            filters=[*self._own(), ("kind", "eq", "revision_accepted")],
            order=[("created_at", "desc")],
            limit=_MAX_REVISIONS,
        )
        learned = build_learned_profile([row["payload"] for row in exams], [row["payload"] for row in revisions])
        await self._upsert({"learned": learned})
        return learned

    async def _upsert(self, values: dict[str, Any]) -> None:
        existing = await self.db.select_one("professor_preferences", columns="professor_id", filters=self._own())
        if existing:
            if values:
                await self.db.update("professor_preferences", values, filters=self._own(), columns="professor_id")
        else:
            await self.db.insert("professor_preferences", values, columns="professor_id")


def build_learned_profile(exams: list[dict[str, Any]], revisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Tendencies from accepted work. Each line says how much evidence supports it."""
    summary: list[str] = []
    learned: dict[str, Any] = {"based_on": len(exams), "accepted_revisions": len(revisions)}
    if exams:
        mcq = round(mean(exam["mcqPercent"] for exam in exams))
        easy = round(mean(exam["easyPercent"] for exam in exams))
        medium = round(mean(exam["mediumPercent"] for exam in exams))
        hard = round(mean(exam["hardPercent"] for exam in exams))
        learned["typical_split"] = {"mcqPercent": mcq, "easyPercent": easy, "mediumPercent": medium, "hardPercent": hard}
        noun = "exam" if len(exams) == 1 else "exams"
        summary.append(f"Typical format across {len(exams)} finalized {noun}: about {mcq}% of marks multiple choice.")
        summary.append(f"Typical difficulty by marks: about {easy}% easy, {medium}% medium, {hard}% hard.")
        durations = [exam["durationMinutes"] for exam in exams if exam.get("durationMinutes")]
        if durations:
            learned["typical_duration"] = median(durations)
            summary.append(f"Typical duration: {median(durations):g} minutes.")
        counts: Counter[str] = Counter()
        for exam in exams:
            counts.update(exam.get("typeCounts") or {})
        if counts:
            total = sum(counts.values())
            common = [f"{name.replace('_', ' ')} ({round(100 * count / total)}%)" for name, count in counts.most_common(3)]
            learned["question_types"] = dict(counts)
            summary.append("Most used question types: " + ", ".join(common) + ".")
        titles = Counter(title for exam in exams for title in exam.get("sectionTitles") or [])
        repeated = [title for title, count in titles.most_common(5) if count >= 2]
        if repeated:
            summary.append("Recurring sections: " + ", ".join(repeated) + ".")
    presets = Counter(revision.get("preset") for revision in revisions if revision.get("preset"))
    tendencies = [
        f"{_PRESET_TENDENCIES[preset]} (accepted {count} times)" for preset, count in presets.most_common() if count >= 2
    ]
    if tendencies:
        learned["tendencies"] = tendencies
        summary.extend(tendencies)
    learned["summary"] = summary
    return learned
