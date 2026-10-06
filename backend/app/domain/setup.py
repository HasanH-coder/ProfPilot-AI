"""An assessment's setup (the Assessment Setup form), with the same rules as the frontend.

Mirrors frontend/src/lib/assessments/draft.ts so that the voice and chat
assistants, which change the setup through this API, can never produce a setup
the form would reject. Every field is optional except the number of versions.
"""

import hashlib
import json
from typing import Any

from app.schemas.common import ApiModel

LIMITS = {
    "exam_name": 120,
    "duration_minutes": 1440,  # 24 hours
    "versions": 20,
    "additional_notes": 2000,
    "professor_prompt": 5000,
}

SETUP_COLUMNS = (
    "course_id, exam_name, duration_minutes, mcq_percentage, subjective_percentage, "
    "number_of_versions, easy_percentage, medium_percentage, hard_percentage, "
    "additional_notes, professor_prompt"
)


class AssessmentSetup(ApiModel):
    """The setup, shaped like the frontend's AssessmentDraft (camelCase in JSON)."""

    course_id: str | None = None
    # As typed. A blank name means "no name" and is stored as null.
    exam_name: str = ""
    duration_minutes: int | None = None
    # Both null, or two whole numbers that add up to 100.
    mcq_percentage: int | None = None
    subjective_percentage: int | None = None
    number_of_versions: int = 1
    # All three null, or three whole numbers that add up to 100.
    easy_percentage: int | None = None
    medium_percentage: int | None = None
    hard_percentage: int | None = None
    additional_notes: str = ""
    professor_prompt: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AssessmentSetup":
        return cls(
            course_id=row.get("course_id"),
            exam_name=row.get("exam_name") or "",
            duration_minutes=row.get("duration_minutes"),
            mcq_percentage=row.get("mcq_percentage"),
            subjective_percentage=row.get("subjective_percentage"),
            number_of_versions=row.get("number_of_versions") or 1,
            easy_percentage=row.get("easy_percentage"),
            medium_percentage=row.get("medium_percentage"),
            hard_percentage=row.get("hard_percentage"),
            additional_notes=row.get("additional_notes") or "",
            professor_prompt=row.get("professor_prompt") or "",
        )

    def to_row(self) -> dict[str, Any]:
        """The exam_projects columns. Blank text is stored as null, never as a placeholder."""
        return {
            "course_id": self.course_id,
            "exam_name": self.exam_name.strip() or None,
            "duration_minutes": self.duration_minutes,
            "mcq_percentage": self.mcq_percentage,
            "subjective_percentage": self.subjective_percentage,
            "number_of_versions": self.number_of_versions,
            "easy_percentage": self.easy_percentage,
            "medium_percentage": self.medium_percentage,
            "hard_percentage": self.hard_percentage,
            "additional_notes": self.additional_notes if self.additional_notes.strip() else None,
            "professor_prompt": self.professor_prompt if self.professor_prompt.strip() else None,
        }

    def errors(self) -> dict[str, str]:
        """An error message per invalid field; empty when the setup is valid."""
        errors: dict[str, str] = {}
        if len(self.exam_name.strip()) > LIMITS["exam_name"]:
            errors["examName"] = f"Use {LIMITS['exam_name']} characters or fewer."
        if self.duration_minutes is not None and not 1 <= self.duration_minutes <= LIMITS["duration_minutes"]:
            errors["durationMinutes"] = "Enter a whole number of minutes, from 1 to 1,440."
        mcq, subjective = self.mcq_percentage, self.subjective_percentage
        if not (mcq is None and subjective is None):
            if mcq is None or subjective is None or not (0 <= mcq <= 100 and 0 <= subjective <= 100) or mcq + subjective != 100:
                errors["distribution"] = "MCQ and subjective percentages must add up to 100%."
        if not 1 <= self.number_of_versions <= LIMITS["versions"]:
            errors["numberOfVersions"] = f"Enter a whole number of versions, from 1 to {LIMITS['versions']}."
        difficulty = check_difficulty_distribution(self.easy_percentage, self.medium_percentage, self.hard_percentage)
        if difficulty:
            errors["difficultyDistribution"] = difficulty
        if len(self.additional_notes) > LIMITS["additional_notes"]:
            errors["additionalNotes"] = "Use 2,000 characters or fewer."
        if len(self.professor_prompt) > LIMITS["professor_prompt"]:
            errors["professorPrompt"] = "Use 5,000 characters or fewer."
        return errors

    def fingerprint(self) -> str:
        """Changes whenever anything an interpretation depends on changes."""
        payload = json.dumps(self.to_row(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    # -- Readable text, for prompts and summaries --------------------------------

    @property
    def duration_text(self) -> str | None:
        return f"{self.duration_minutes} minutes" if self.duration_minutes else None

    @property
    def format_text(self) -> str | None:
        if self.mcq_percentage is None or self.subjective_percentage is None:
            return None
        return f"{self.mcq_percentage}% MCQ / {self.subjective_percentage}% subjective"

    @property
    def difficulty_text(self) -> str | None:
        if self.easy_percentage is None or self.medium_percentage is None or self.hard_percentage is None:
            return None
        return f"{self.easy_percentage}% easy / {self.medium_percentage}% medium / {self.hard_percentage}% hard"


def check_difficulty_distribution(easy: int | None, medium: int | None, hard: int | None) -> str | None:
    values = [easy, medium, hard]
    if all(value is None for value in values):
        return None
    if any(value is None or not 0 <= value <= 100 for value in values):
        return "Enter a whole number from 0 to 100 for each difficulty."
    if sum(values) != 100:  # type: ignore[arg-type]
        return "Difficulty percentages must total 100%."
    return None
