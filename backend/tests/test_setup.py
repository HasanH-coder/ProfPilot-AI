"""The setup rules (mirroring the form) and the 'Set up with AI' tools."""

import pytest

from app.core.errors import InvalidInputError
from app.domain.setup import AssessmentSetup
from app.services.assessment_context import CourseInfo
from app.services.setup_assistant import SETUP_TOOLS, execute_setup_tool

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
