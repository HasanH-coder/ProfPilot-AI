"""An assessment's setup (the Assessment Setup form), with the same rules as the frontend.

Mirrors frontend/src/lib/assessments/draft.ts so that the voice and chat
assistants, which change the setup through this API, can never produce a setup
the form would reject. Every field is optional except the number of versions.
"""

import hashlib
import json
from typing import Any

from app.schemas.ai import COGNITIVE_LEVELS
from app.schemas.common import ApiModel

LIMITS = {
    "exam_name": 120,
    "duration_minutes": 1440,  # 24 hours
    "versions": 20,
    "additional_notes": 2000,
    "professor_prompt": 5000,
}

# A 10-minute assessment is a mini quiz, with this many questions unless the professor asks otherwise.
MINI_QUIZ_MINUTES = 10
MINI_QUIZ_QUESTIONS = 5

SETUP_COLUMNS = (
    "course_id, exam_name, duration_minutes, mcq_percentage, subjective_percentage, "
    "number_of_versions, easy_percentage, medium_percentage, hard_percentage, difficulty_definitions, "
    "learning_focus, bloom_distribution, additional_notes, professor_prompt"
)

DIFFICULTIES = ("easy", "medium", "hard")
DIFFICULTY_DEFINITION_LIMIT = 400
# What easy, medium and hard mean unless the professor writes their own. Difficulty is the thinking a
# question demands, never longer wording. The shares of prepared students expected to answer correctly
# follow the usual item-difficulty bands (about 70% or more easy, 30-70% medium, under 30% hard); they
# state an intent, because the real figure is only known after students sit the exam.
# Mirrored in frontend/src/lib/assessments/draft.ts.
DEFAULT_DIFFICULTY_DEFINITIONS: dict[str, str] = {
    "easy": (
        "One idea taught directly in the course, asked in the form it was taught: recalling, recognising or "
        "plainly applying it. About 70% or more of prepared students should answer it correctly."
    ),
    "medium": (
        "A taught idea used or explained in a familiar situation, or two taught ideas linked, with some "
        "interpretation or justification. About 30% to 70% of prepared students should answer it correctly."
    ),
    "hard": (
        "Several ideas combined, or taught ideas applied to a new situation, with judgement between close "
        "alternatives; yet fair and answerable from the course material. About 30% or fewer of prepared "
        "students should answer it correctly."
    ),
}

LEARNING_FOCUSES = ("automatic", "balanced", "practical", "critical", "custom")
LEARNING_FOCUS_LABELS = {
    "automatic": "Automatic",
    "balanced": "Balanced",
    "practical": "Practical & Problem-Solving",
    "critical": "Critical Thinking",
    "custom": "Custom",
}
# The splits (by marks) the presets stand for. Suggested starting points, not
# pedagogical standards. Mirrored in frontend/src/lib/assessments/draft.ts.
BLOOM_PRESETS: dict[str, dict[str, int]] = {
    "balanced": {"remember": 15, "understand": 20, "apply": 25, "analyze": 20, "evaluate": 10, "create": 10},
    "practical": {"remember": 5, "understand": 15, "apply": 40, "analyze": 25, "evaluate": 10, "create": 5},
    "critical": {"remember": 5, "understand": 10, "apply": 20, "analyze": 30, "evaluate": 25, "create": 10},
}


# Short forms of the defaults, for the end of the improved prompt (the full texts above go to the writer).
DEFAULT_DIFFICULTY_SUMMARIES: dict[str, str] = {
    "easy": (
        "one idea asked as it was taught: recall, recognise or plainly apply it "
        "(about 70% or more of prepared students answer correctly)"
    ),
    "medium": (
        "a taught idea used in a familiar situation, or two ideas linked, with some interpretation "
        "(about 30% to 70% answer correctly)"
    ),
    "hard": (
        "several ideas combined, or a new situation needing judgement between close options (about 30% or fewer answer correctly)"
    ),
}
DIFFICULTY_MEANING_LABEL = "What the levels mean"


def with_difficulty_meaning(prompt: str, setup: "AssessmentSetup") -> str:
    """The improved prompt with the meaning of easy, medium and hard added at its end, once."""
    prompt = prompt.strip()
    if DIFFICULTY_MEANING_LABEL.lower() in prompt.lower():
        return prompt
    return f"{prompt}\n\n{setup.difficulty_meaning_line}"


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
    # The professor's own meaning of easy, medium and hard ({"easy": …, "medium": …, "hard": …}); null means
    # DEFAULT_DIFFICULTY_DEFINITIONS.
    difficulty_definitions: dict[str, str] | None = None
    # One of LEARNING_FOCUSES. Only "custom" has its own percentages (by Bloom's level).
    learning_focus: str = "automatic"
    bloom_distribution: dict[str, int] | None = None
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
            difficulty_definitions=row.get("difficulty_definitions"),
            learning_focus=row.get("learning_focus") or "automatic",
            bloom_distribution=row.get("bloom_distribution"),
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
            "difficulty_definitions": self.difficulty_definitions,
            "learning_focus": self.learning_focus,
            "bloom_distribution": self.bloom_distribution,
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
        definitions = check_difficulty_definitions(self.difficulty_definitions)
        if definitions:
            errors["difficultyDefinitions"] = definitions
        bloom = check_bloom_distribution(self.learning_focus, self.bloom_distribution)
        if bloom:
            errors["bloomDistribution"] = bloom
        if len(self.additional_notes) > LIMITS["additional_notes"]:
            errors["additionalNotes"] = "Use 2,000 characters or fewer."
        if len(self.professor_prompt) > LIMITS["professor_prompt"]:
            errors["professorPrompt"] = "Use 5,000 characters or fewer."
        return errors

    def fingerprint(self) -> str:
        """Changes whenever anything an interpretation depends on changes."""
        row = self.to_row()
        # The default learning focus is left out, so interpretations made before
        # the setting existed keep matching their unchanged assessments.
        if row["learning_focus"] == "automatic" and row["bloom_distribution"] is None:
            del row["learning_focus"], row["bloom_distribution"]
        # Likewise the default definitions, which older interpretations were made under.
        if row["difficulty_definitions"] is None:
            del row["difficulty_definitions"]
        payload = json.dumps(row, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def resolved_bloom(self) -> dict[str, int] | None:
        """The split by Bloom's level the professor chose (a preset or their own); None for automatic."""
        if self.learning_focus == "custom":
            return self.bloom_distribution
        return BLOOM_PRESETS.get(self.learning_focus)

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

    @property
    def custom_difficulty_definitions(self) -> dict[str, str] | None:
        """The professor's own definitions, trimmed; None when they use ProfPilot's defaults."""
        if not self.difficulty_definitions:
            return None
        return {level: self.difficulty_definitions[level].strip() for level in DIFFICULTIES}

    @property
    def difficulty_definitions_text(self) -> str:
        """What easy, medium and hard mean here, one line each, for prompts and the brief."""
        custom = self.custom_difficulty_definitions
        definitions = custom or DEFAULT_DIFFICULTY_DEFINITIONS
        return "\n".join(f"  {level.capitalize()}: {definitions[level]}" for level in DIFFICULTIES)

    @property
    def difficulty_meaning_line(self) -> str:
        """One line for the end of the improved prompt, so the professor reads what each level means.
        The professor's own words when they wrote them, otherwise short forms of the defaults."""
        custom = self.custom_difficulty_definitions
        meanings = custom or DEFAULT_DIFFICULTY_SUMMARIES
        parts = [f"{level.capitalize()}: {meanings[level].rstrip('. ')}." for level in DIFFICULTIES]
        label = DIFFICULTY_MEANING_LABEL + (" (your definitions)" if custom else "")
        return f"{label}: " + " ".join(parts)

    @property
    def learning_focus_text(self) -> str:
        label = LEARNING_FOCUS_LABELS.get(self.learning_focus, self.learning_focus)
        shares = self.resolved_bloom()
        if shares is None:
            return f"{label} (no split given: recommend one that suits the material, objectives and formats)"
        split = " / ".join(f"{shares[level]}% {level}" for level in COGNITIVE_LEVELS)
        kind = "the professor's own split" if self.learning_focus == "custom" else "ProfPilot's preset the professor chose"
        return f"{label}, {kind}: {split}"


def check_bloom_distribution(focus: str, distribution: dict[str, int] | None) -> str | None:
    """What's wrong with a learning focus, if anything. Only a custom focus has
    percentages: six whole numbers from 0 to 100 that add up to 100. The database
    enforces the same rule."""
    if focus not in LEARNING_FOCUSES:
        return "Choose one of the learning focus options."
    if focus != "custom":
        return None if distribution is None else "Only a custom learning focus has its own percentages."
    if distribution is None or set(distribution) != set(COGNITIVE_LEVELS):
        return "Enter a percentage for each of the six levels."
    values = list(distribution.values())
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100 for value in values):
        return "Enter a whole number from 0 to 100 for each level."
    if sum(values) != 100:
        return "Bloom's percentages must total 100%."
    return None


def check_difficulty_definitions(definitions: dict[str, str] | None) -> str | None:
    """What's wrong with the professor's own definitions, if anything: all three, each text, not too long."""
    if definitions is None:
        return None
    if set(definitions) != set(DIFFICULTIES):
        return "Write a definition for each of easy, medium and hard."
    for text in definitions.values():
        if not isinstance(text, str) or not text.strip():
            return "Write a definition for each of easy, medium and hard."
        if len(text.strip()) > DIFFICULTY_DEFINITION_LIMIT:
            return f"Use {DIFFICULTY_DEFINITION_LIMIT} characters or fewer for each definition."
    return None


def check_difficulty_distribution(easy: int | None, medium: int | None, hard: int | None) -> str | None:
    values = [easy, medium, hard]
    if all(value is None for value in values):
        return None
    if any(value is None or not 0 <= value <= 100 for value in values):
        return "Enter a whole number from 0 to 100 for each difficulty."
    if sum(values) != 100:  # type: ignore[arg-type]
        return "Difficulty percentages must total 100%."
    return None
