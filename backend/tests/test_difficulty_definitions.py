"""The professor's own definitions of easy, medium and hard: validation, what prompts say, and staleness."""

import pytest

from app.domain.setup import (
    DEFAULT_DIFFICULTY_DEFINITIONS,
    DIFFICULTY_DEFINITION_LIMIT,
    AssessmentSetup,
    with_difficulty_meaning,
)
from app.services.assessment_context import AssessmentRecord, render_difficulty_definitions, render_settings

CUSTOM = {
    "easy": "A student who read the notes answers it from memory.",
    "medium": "One short calculation from given values.",
    "hard": "Combines two chapters in a new setting.",
}


def record_for(setup: AssessmentSetup) -> AssessmentRecord:
    return AssessmentRecord(
        id="a1",
        setup=setup,
        course=None,
        status="draft",
        updated_at="2026-11-07T00:00:00Z",
        enhanced_prompt=None,
        exam_spec=None,
        spec_status="none",
        spec_generated_at=None,
        spec_approved_at=None,
        generation_mode="full",
    )


def test_no_definitions_means_the_defaults():
    setup = AssessmentSetup()
    assert setup.difficulty_definitions is None and setup.custom_difficulty_definitions is None
    assert setup.errors() == {}
    for level in ("easy", "medium", "hard"):
        assert DEFAULT_DIFFICULTY_DEFINITIONS[level] in setup.difficulty_definitions_text


def test_the_defaults_state_what_students_should_get_right():
    assert "70%" in DEFAULT_DIFFICULTY_DEFINITIONS["easy"]
    assert "30%" in DEFAULT_DIFFICULTY_DEFINITIONS["medium"] and "70%" in DEFAULT_DIFFICULTY_DEFINITIONS["medium"]
    assert "30%" in DEFAULT_DIFFICULTY_DEFINITIONS["hard"]


@pytest.mark.parametrize(
    ("definitions", "valid"),
    [
        (None, True),
        (CUSTOM, True),
        ({**CUSTOM, "hard": "x" * DIFFICULTY_DEFINITION_LIMIT}, True),
        ({**CUSTOM, "hard": "x" * (DIFFICULTY_DEFINITION_LIMIT + 1)}, False),
        ({**CUSTOM, "easy": "   "}, False),
        ({"easy": "a", "medium": "b"}, False),
        ({**CUSTOM, "extra": "c"}, False),
    ],
)
def test_definitions_must_be_all_three_and_not_too_long(definitions, valid):
    setup = AssessmentSetup(difficulty_definitions=definitions)
    assert ("difficultyDefinitions" not in setup.errors()) is valid


def test_definitions_round_trip_through_the_row():
    row = AssessmentSetup(difficulty_definitions=CUSTOM).to_row()
    assert row["difficulty_definitions"] == CUSTOM
    assert AssessmentSetup.from_row(row).difficulty_definitions == CUSTOM
    assert AssessmentSetup().to_row()["difficulty_definitions"] is None


def test_changing_the_definitions_makes_the_interpretation_stale():
    plain = AssessmentSetup()
    custom = AssessmentSetup(difficulty_definitions=CUSTOM)
    changed = AssessmentSetup(difficulty_definitions={**CUSTOM, "hard": "Something else entirely."})
    assert len({plain.fingerprint(), custom.fingerprint(), changed.fingerprint()}) == 3


def test_the_settings_give_the_interpreter_the_definitions_in_use():
    defaults = render_settings(record_for(AssessmentSetup()))
    assert "ProfPilot's defaults" in defaults and DEFAULT_DIFFICULTY_DEFINITIONS["hard"] in defaults

    own = render_settings(record_for(AssessmentSetup(difficulty_definitions=CUSTOM)))
    assert "the professor's own definitions" in own and CUSTOM["medium"] in own
    assert DEFAULT_DIFFICULTY_DEFINITIONS["medium"] not in own


def test_the_improved_prompt_ends_with_what_the_levels_mean():
    brief = "Write a short quiz on probability."
    default = with_difficulty_meaning(brief, AssessmentSetup())
    assert default.startswith(brief) and default.rstrip().endswith(".")
    assert "What the levels mean:" in default and "(your definitions)" not in default
    for level in ("Easy:", "Medium:", "Hard:"):
        assert level in default

    own = with_difficulty_meaning(brief, AssessmentSetup(difficulty_definitions=CUSTOM))
    assert "(your definitions)" in own and CUSTOM["hard"].rstrip(".") in own


def test_the_meaning_line_is_added_once_and_never_to_an_edited_prompt_that_has_it():
    setup = AssessmentSetup()
    once = with_difficulty_meaning("A brief.", setup)
    assert with_difficulty_meaning(once, setup) == once
    mine = "My brief. What the levels mean: easy is easy."
    assert with_difficulty_meaning(mine, setup) == mine


def test_the_writer_and_reviewer_hear_only_the_professors_own_definitions():
    assert render_difficulty_definitions(AssessmentSetup()) == ""
    text = render_difficulty_definitions(AssessmentSetup(difficulty_definitions=CUSTOM))
    assert "binding" in text and all(definition in text for definition in CUSTOM.values())
