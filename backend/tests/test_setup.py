"""The setup rules (mirroring the form) and the 'Set up with AI' tools."""

import pytest

from app.core.errors import InvalidInputError
from app.domain.setup import AssessmentSetup
from app.services.assessment_context import CourseInfo
from app.services.setup_assistant import SETUP_TOOLS, execute_setup_tool, next_setup_question

COURSES = [CourseInfo(id="c-1", code="CMPS 297U", name="Machine Learning")]


def test_an_entirely_empty_setup_is_valid():
    setup = AssessmentSetup()
    assert setup.errors() == {}
    row = setup.to_row()
    assert row["exam_name"] is None and row["course_id"] is None  # never "Untitled assessment"
    assert row["number_of_versions"] == 1


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"easy_percentage": 30, "medium_percentage": 30, "hard_percentage": 30}, "difficultyDistribution"),
        ({"easy_percentage": 30, "medium_percentage": None, "hard_percentage": 70}, "difficultyDistribution"),
        ({"mcq_percentage": 40, "subjective_percentage": 50}, "distribution"),
        ({"number_of_versions": 0}, "numberOfVersions"),
        ({"duration_minutes": 0}, "durationMinutes"),
        ({"exam_name": "x" * 121}, "examName"),
    ],
)
def test_invalid_setups_are_reported(values, field):
    assert field in AssessmentSetup(**values).errors()


@pytest.mark.parametrize("split", [(0, 50, 50), (100, 0, 0), (10, 20, 70), (30, 40, 30)])
def test_valid_difficulty_distributions(split):
    easy, medium, hard = split
    assert AssessmentSetup(easy_percentage=easy, medium_percentage=medium, hard_percentage=hard).errors() == {}


def test_voice_tools_update_the_setup_like_the_form_would():
    setup = AssessmentSetup()
    setup, result = execute_setup_tool(setup, "set_duration", {"minutes": 90}, COURSES)
    assert setup.duration_minutes == 90 and result["changedFields"] == ["durationMinutes"]
    setup, _ = execute_setup_tool(
        setup, "set_difficulty_distribution", {"easy_percent": 30, "medium_percent": 40, "hard_percent": 30}, COURSES
    )
    assert (setup.easy_percentage, setup.medium_percentage, setup.hard_percentage) == (30, 40, 30)
    setup, _ = execute_setup_tool(setup, "set_question_distribution", {"mcq_percent": 20}, COURSES)
    assert (setup.mcq_percentage, setup.subjective_percentage) == (20, 80)
    setup, _ = execute_setup_tool(setup, "set_versions", {"count": 2}, COURSES)
    setup, _ = execute_setup_tool(setup, "set_course", {"course_id": "c-1"}, COURSES)
    assert setup.course_id == "c-1" and setup.number_of_versions == 2
    setup, _ = execute_setup_tool(setup, "update_notes", {"text": "Focus on lectures 3 to 5.", "mode": "append"}, COURSES)
    setup, result = execute_setup_tool(setup, "update_notes", {"text": "Not too theoretical.", "mode": "append"}, COURSES)
    assert setup.additional_notes == "Focus on lectures 3 to 5.\nNot too theoretical."
    assert setup.errors() == {}


def test_overwrites_report_the_previous_value():
    setup = AssessmentSetup(duration_minutes=60)
    _, result = execute_setup_tool(setup, "set_duration", {"minutes": 90}, COURSES)
    assert result["previousValues"] == {"durationMinutes": 60}


def test_clearing_values():
    setup = AssessmentSetup(duration_minutes=60, easy_percentage=30, medium_percentage=40, hard_percentage=30)
    setup, _ = execute_setup_tool(setup, "set_duration", {"minutes": None}, COURSES)
    setup, _ = execute_setup_tool(
        setup, "set_difficulty_distribution", {"easy_percent": None, "medium_percent": None, "hard_percent": None}, COURSES
    )
    assert setup.duration_minutes is None and setup.easy_percentage is None


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("set_difficulty_distribution", {"easy_percent": 30, "medium_percent": 30, "hard_percent": 30}),
        ("set_difficulty_distribution", {"easy_percent": 30, "medium_percent": None, "hard_percent": 70}),
        ("set_question_distribution", {"mcq_percent": 120}),
        ("set_versions", {"count": 0}),
        ("set_duration", {"minutes": 5000}),
        ("set_course", {"course_id": "someone-elses-course"}),
        ("set_assessment_name", {"name": "x" * 200}),
        ("delete_everything", {}),
    ],
)
def test_tools_never_bypass_validation(tool, arguments):
    with pytest.raises(InvalidInputError):
        execute_setup_tool(AssessmentSetup(), tool, arguments, COURSES)


def test_tool_schemas_are_strict():
    for tool in SETUP_TOOLS:
        parameters = tool["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])


# -- Exact difficulty values (the professor's numbers are final) --------------------------


@pytest.mark.parametrize("split", [(30, 40, 30), (20, 50, 30), (0, 60, 40), (100, 0, 0)])
def test_difficulty_is_set_exactly_as_stated(split):
    easy, medium, hard = split
    setup, result = execute_setup_tool(
        AssessmentSetup(),
        "set_difficulty_distribution",
        {"easy_percent": easy, "medium_percent": medium, "hard_percent": hard},
        COURSES,
    )
    assert (setup.easy_percentage, setup.medium_percentage, setup.hard_percentage) == split
    assert result["message"] == f"Difficulty set to {easy}% easy, {medium}% medium and {hard}% hard."


@pytest.mark.parametrize(
    "arguments",
    [
        {"easy_percent": "30", "medium_percent": "40", "hard_percent": "30"},
        {"easy_percent": "30%", "medium_percent": "40%", "hard_percent": "30%"},
        {"easy_percent": 30.0, "medium_percent": 40.0, "hard_percent": 30.0},
        {"easy_percent": 0.3, "medium_percent": 0.4, "hard_percent": 0.3},
    ],
)
def test_the_same_numbers_in_another_form_are_the_same_split(arguments):
    # Voice tools aren't schema-checked, so the model may send strings, floats or fractions.
    setup, _ = execute_setup_tool(AssessmentSetup(), "set_difficulty_distribution", arguments, COURSES)
    assert (setup.easy_percentage, setup.medium_percentage, setup.hard_percentage) == (30, 40, 30)


@pytest.mark.parametrize(
    ("split", "expected"),
    [
        ((30, 30, 30), ["adds up to 90%", "What should the remaining 10% be?"]),
        ((40, 40, 30), ["adds up to 110%", "Which share should be lower?"]),
        ((50, 0, 0), ["adds up to 50%", "remaining 50%"]),
    ],
)
def test_a_split_that_does_not_add_up_is_never_normalized(split, expected):
    easy, medium, hard = split
    start = AssessmentSetup(easy_percentage=20, medium_percentage=60, hard_percentage=20)
    with pytest.raises(InvalidInputError) as raised:
        execute_setup_tool(
            start, "set_difficulty_distribution", {"easy_percent": easy, "medium_percent": medium, "hard_percent": hard}, COURSES
        )
    for text in [*expected, "Nothing was changed", "Don't change their numbers"]:
        assert text in raised.value.message
    # The executor is pure: the professor's existing split is untouched.
    assert (start.easy_percentage, start.medium_percentage, start.hard_percentage) == (20, 60, 20)


def test_a_missing_share_is_asked_for_not_assumed():
    with pytest.raises(InvalidInputError) as raised:
        execute_setup_tool(
            AssessmentSetup(),
            "set_difficulty_distribution",
            {"easy_percent": 30, "medium_percent": 40, "hard_percent": None},
            COURSES,
        )
    message = raised.value.message
    assert "30% easy and 40% medium but no hard share" in message and "the rest would be 30%" in message
    assert "don't assume it" in message


def test_shares_must_be_whole_numbers():
    with pytest.raises(InvalidInputError) as raised:
        execute_setup_tool(
            AssessmentSetup(),
            "set_difficulty_distribution",
            {"easy_percent": 33.3, "medium_percent": 33.3, "hard_percent": 33.4},
            COURSES,
        )
    assert "whole number" in raised.value.message


def test_the_multiple_choice_share_is_also_taken_exactly():
    setup, _ = execute_setup_tool(AssessmentSetup(), "set_question_distribution", {"mcq_percent": "35"}, COURSES)
    assert (setup.mcq_percentage, setup.subjective_percentage) == (35, 65)


# -- One question at a time; skipping leaves a setting empty -------------------------------


def test_the_assistant_covers_one_setting_at_a_time_in_order():
    setup = AssessmentSetup()
    addressed: list[str] = []
    asked = [next_setup_question(setup, COURSES, addressed)]
    answers = [
        ("set_course", {"course_id": "c-1"}),
        ("set_assessment_name", {"name": "Midterm"}),
        ("skip_setting", {"setting": "duration"}),
        ("set_question_distribution", {"mcq_percent": 40}),
        ("set_difficulty_distribution", {"easy_percent": 30, "medium_percent": 40, "hard_percent": 30}),
        ("set_versions", {"count": 1}),
        ("update_notes", {"text": "Lectures 3 to 5.", "mode": "append"}),
    ]
    for name, arguments in answers:
        setup, result = execute_setup_tool(setup, name, arguments, COURSES, addressed)
        addressed = result["addressed"]
        asked.append(result["nextQuestion"])
    assert asked == [
        "What course is this for?",
        "What type of assessment is it?",
        "Do you want to specify a duration?",
        "Do you want to specify the question format?",
        "Do you want to set a difficulty mix?",
        "How many versions do you need?",
        "What should it cover?",
        "Anything else, or shall I finish?",
    ]
    # Each turn asks exactly one question.
    assert all(question.count("?") == 1 for question in asked)
    # The skipped duration stays empty and is never asked about again.
    assert setup.duration_minutes is None and "duration" in addressed


def test_skipping_never_sets_or_clears_a_value():
    setup, result = execute_setup_tool(AssessmentSetup(), "skip_setting", {"setting": "difficulty"}, COURSES)
    assert setup == AssessmentSetup() and result["changedFields"] == []
    assert result["addressed"] == ["difficulty"]
    filled = AssessmentSetup(duration_minutes=60)
    kept, _ = execute_setup_tool(filled, "skip_setting", {"setting": "duration"}, COURSES)
    assert kept.duration_minutes == 60


def test_settings_already_filled_in_are_not_asked_about():
    setup = AssessmentSetup(course_id="c-1", exam_name="Quiz 2", duration_minutes=45)
    assert next_setup_question(setup, COURSES, []) == "Do you want to specify the question format?"
    # Without any courses there is nothing to choose, so the course isn't asked about.
    assert next_setup_question(AssessmentSetup(), [], []) == "What type of assessment is it?"


def test_setup_instructions_ask_one_short_question_and_keep_numbers_exact():
    from app.ai.prompts import SETUP_GUIDE, setup_chat_instructions, setup_voice_instructions

    for rule in (
        "ONE QUESTION AT A TIME",
        "never several (not duration, versions, difficulty and format together)",
        "call skip_setting",
        "Never invent a value",
        "call finish_setup right away",
        "never rounded, rebalanced",
        "'That adds up to 90%. What should the remaining 10% be?'",
        "as a suggestion",
        "No filler",
        "Done. How many versions?",
    ):
        assert rule in SETUP_GUIDE, rule
    # The old rule that let the assistant pick a split itself is gone.
    assert "choose a sensible split" not in SETUP_GUIDE
    voice = setup_voice_instructions("- Course: not set", "- CMPS 297U: id c-1", "What course is this for?")
    assert "the first question only, e.g. 'Hi. What course is this for?'" in voice
    chat = setup_chat_instructions("- Course: not set", "- CMPS 297U: id c-1", "What course is this for?")
    assert "NEXT QUESTION, if the professor's message doesn't change the topic: What course is this for?" in chat
