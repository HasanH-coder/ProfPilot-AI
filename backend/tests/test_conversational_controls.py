"""Voice and chat controls for the learning focus, course sources, and undo while building."""

import asyncio

import pytest

from app.core.errors import InvalidInputError
from app.domain.setup import AssessmentSetup
from app.services.assessment_context import CourseInfo
from app.services.setup_assistant import SETUP_TOOLS, describe_setup, execute_setup_tool, next_setup_question
from tests.helpers import auth
from tests.seed import seed_course, seed_course_source
from tests.test_builder import NULL_QUESTION, start_builder

COURSES = [CourseInfo(id="c-1", code="CMPS 297U", name="Machine Learning")]
SIX = ("remember", "understand", "apply", "analyze", "evaluate", "create")


def run(coroutine):
    return asyncio.run(coroutine)


def focus(name: str, *shares: int | None) -> dict:
    values = shares or (None,) * 6
    return {"focus": name, **{f"{level}_percent": value for level, value in zip(SIX, values, strict=True)}}


# -- Learning focus (Bloom's Taxonomy) ------------------------------------------------------


@pytest.mark.parametrize("preset", ["balanced", "practical", "critical"])
def test_a_preset_focus_is_set_without_percentages(preset):
    setup, result = execute_setup_tool(AssessmentSetup(), "set_learning_focus", focus(preset), COURSES)
    assert setup.learning_focus == preset and setup.bloom_distribution is None
    assert result["changedFields"] == ["learningFocus"]
    assert "% remember" in result["setup"]  # the preset's split is visible to the assistant


def test_a_custom_split_is_kept_exactly_as_said():
    setup, result = execute_setup_tool(AssessmentSetup(), "set_learning_focus", focus("custom", 5, 10, 35, 30, 15, 5), COURSES)
    assert setup.learning_focus == "custom"
    assert setup.bloom_distribution == dict(zip(SIX, (5, 10, 35, 30, 15, 5), strict=True))
    assert result["changedFields"] == ["learningFocus", "bloomDistribution"]
    assert setup.errors() == {}


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (focus("custom", 10, 10, 30, 30, 10, 5), ["adds up to 95%", "remaining 5%", "Nothing was changed"]),
        (focus("custom", 10, 10, 30, 30, 10, None), ["no create share", "the rest would be 10%"]),
        (focus("custom"), ["share of marks at each of the six levels"]),
        (focus("balanced", 10, 10, 30, 30, 10, 10), ["only for a custom focus"]),
        ({**focus("custom"), "focus": "creative"}, ["Choose automatic"]),
    ],
)
def test_a_focus_the_form_would_reject_is_asked_about_never_adjusted(arguments, expected):
    start = AssessmentSetup(learning_focus="practical")
    with pytest.raises(InvalidInputError) as raised:
        execute_setup_tool(start, "set_learning_focus", arguments, COURSES)
    for text in expected:
        assert text in raised.value.message
    assert start.learning_focus == "practical" and start.bloom_distribution is None


def test_back_to_automatic_clears_a_custom_split():
    custom = AssessmentSetup(learning_focus="custom", bloom_distribution=dict(zip(SIX, (5, 10, 35, 30, 15, 5), strict=True)))
    setup, _ = execute_setup_tool(custom, "set_learning_focus", focus("automatic"), COURSES)
    assert setup.learning_focus == "automatic" and setup.bloom_distribution is None


def test_the_focus_is_asked_once_and_skipping_keeps_the_professors_choice():
    after_difficulty = ["course", "name", "duration", "question_format", "difficulty"]
    asked = "Do you want a learning focus, such as practical problem-solving or critical thinking?"
    assert next_setup_question(AssessmentSetup(course_id="c-1"), COURSES, after_difficulty) == asked
    # Skipped: never asked again, and an existing focus is left as it is.
    chosen = AssessmentSetup(course_id="c-1", learning_focus="critical")
    kept, result = execute_setup_tool(chosen, "skip_setting", {"setting": "learning_focus"}, COURSES, after_difficulty)
    assert kept.learning_focus == "critical" and result["changedFields"] == []
    assert result["nextQuestion"] == "How many versions do you need?"
    # A focus already chosen on the form isn't asked about at all.
    assert next_setup_question(chosen, COURSES, after_difficulty) == "How many versions do you need?"


def test_the_assistant_sees_the_focus_and_the_source_count():
    text = describe_setup(AssessmentSetup(learning_focus="practical"), COURSES, selected_sources=2)
    assert "- Learning focus: Practical & Problem-Solving (5% remember / 15% understand / 40% apply" in text
    assert "- Course sources selected: 2" in text
    assert "- Learning focus: Automatic" in describe_setup(AssessmentSetup(), COURSES)


def test_new_tool_schemas_are_strict():
    tools = {tool["name"]: tool for tool in SETUP_TOOLS}
    for name in ("set_learning_focus", "list_course_sources", "set_course_sources"):
        parameters = tools[name]["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])


def test_the_voice_endpoint_sets_a_custom_focus(api, db_a):
    response = api.post(
        "/api/setup-assistant/tools/set_learning_focus",
        headers=auth(),
        json={"setup": {}, "arguments": focus("custom", 0, 20, 40, 20, 10, 10)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["setup"]["learningFocus"] == "custom" and body["setup"]["bloomDistribution"]["apply"] == 40


# -- Course sources -------------------------------------------------------------------------


@pytest.fixture
def library(supabase, db_a, db_b):
    """Two ready sources in course 1, one still being read, one in course 2, and another professor's."""
    course, other_course = run(seed_course(db_a)), run(seed_course(db_a, code="STAT 202"))
    ready = [run(seed_course_source(supabase, db_a, course["id"], name=f"Lecture {n}.pdf")) for n in (1, 2)]
    reading = run(seed_course_source(supabase, db_a, course["id"], name="Lecture 3.pdf"))
    elsewhere = run(seed_course_source(supabase, db_a, other_course["id"], name="Stats.pdf"))
    for source in [*ready, elsewhere]:
        run(db_a.update("documents", {"processing_status": "ready"}, filters=[("id", "eq", source["id"])]))
    their_course = run(seed_course(db_b, code="THEIRS 1"))
    theirs = run(seed_course_source(supabase, db_b, their_course["id"], name="Theirs.pdf"))
    run(db_b.update("documents", {"processing_status": "ready"}, filters=[("id", "eq", theirs["id"])]))
    return {
        "course": course,
        "other_course": other_course,
        "ready": ready,
        "reading": reading,
        "elsewhere": elsewhere,
        "theirs": theirs,
    }


def tool(api, name, setup, arguments=None, source_ids=None, token="token-a"):
    return api.post(
        f"/api/setup-assistant/tools/{name}",
        headers=auth(token),
        json={"setup": setup, "arguments": arguments or {}, "sourceIds": source_ids or []},
    )


def test_only_the_courses_ready_sources_are_listed(api, library):
    body = tool(api, "list_course_sources", {"courseId": library["course"]["id"]}).json()
    listed = body["result"]["sources"]
    assert [s["id"] for s in listed] == [s["id"] for s in library["ready"]]
    assert all(s["selected"] is False for s in listed) and body["result"]["changedFields"] == []


def test_sources_are_selected_by_listed_ids_and_reported_as_a_change(api, library):
    course = {"courseId": library["course"]["id"]}
    first, second = (s["id"] for s in library["ready"])
    chosen = tool(api, "set_course_sources", course, {"source_ids": [first], "mode": "replace"}).json()["result"]
    assert chosen["sourceIds"] == [first] and chosen["changedFields"] == ["sourceIds"]
    added = tool(api, "set_course_sources", course, {"source_ids": [second], "mode": "add"}, [first]).json()["result"]
    assert added["sourceIds"] == [first, second]
    removed = tool(api, "set_course_sources", course, {"source_ids": [first], "mode": "remove"}, [first, second]).json()["result"]
    assert removed["sourceIds"] == [second]


@pytest.mark.parametrize("which", ["reading", "elsewhere", "theirs", "invented"])
def test_ids_that_arent_the_courses_ready_sources_are_refused(api, library, which):
    forged = "00000000-0000-4000-8000-000000000000" if which == "invented" else library[which]["id"]
    response = tool(api, "set_course_sources", {"courseId": library["course"]["id"]}, {"source_ids": [forged], "mode": "add"})
    assert response.status_code == 422
    assert "Use only ids from list_course_sources" in response.text


def test_a_forged_selection_from_the_browser_is_dropped(api, library):
    first, second = (s["id"] for s in library["ready"])
    result = tool(
        api,
        "set_course_sources",
        {"courseId": library["course"]["id"]},
        {"source_ids": [second], "mode": "add"},
        [library["theirs"]["id"], first],
    ).json()["result"]
    assert result["sourceIds"] == [first, second]


@pytest.mark.parametrize("course_id", ["not-an-id", "00000000-0000-4000-8000-000000000000"])
def test_a_course_that_isnt_the_professors_has_no_sources_and_no_query_error(api, library, course_id):
    body = tool(api, "list_course_sources", {"courseId": course_id})
    assert body.status_code == 200 and body.json()["result"]["sources"] == []


def test_malformed_source_ids_never_clear_the_selection(api, library):
    first = library["ready"][0]["id"]
    response = tool(
        api, "set_course_sources", {"courseId": library["course"]["id"]}, {"source_ids": first, "mode": "replace"}, [first]
    )
    assert response.status_code == 422 and "as a list" in response.text


def test_another_professor_sees_none_of_the_sources(api, library):
    body = tool(api, "list_course_sources", {"courseId": library["course"]["id"]}, token="token-b").json()
    assert body["result"]["sources"] == []


def test_sources_need_a_course_and_another_course_starts_with_none(api, library):
    no_course = tool(api, "list_course_sources", {})
    assert no_course.status_code == 422 and "Choose a course first" in no_course.text
    first = library["ready"][0]["id"]
    moved = tool(api, "set_course", {"courseId": library["course"]["id"]}, {"course_id": library["other_course"]["id"]}, [first])
    result = moved.json()["result"]
    assert result["sourceIds"] == [] and set(result["changedFields"]) == {"courseId", "sourceIds"}


def test_the_text_chat_selects_sources_and_returns_the_selection(api, ai, library):
    first = library["ready"][0]["id"]

    def script(messages):
        return [("list_course_sources", {}), ("set_course_sources", {"source_ids": [first], "mode": "add"})], "Done."

    ai.tool_scripts["setup_chat"] = script
    response = api.post(
        "/api/setup-assistant/messages",
        headers=auth(),
        json={"setup": {"courseId": library["course"]["id"]}, "messages": [{"role": "professor", "content": "Use lecture 1."}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sourceIds"] == [first] and body["changedFields"] == ["sourceIds"]


# -- Build with AI: undo, and what the assistant knows ---------------------------------------


def builder_tool(api, exam_id, name, arguments):
    response = api.post(f"/api/exams/{exam_id}/builder/tools/{name}", headers=auth(), json={"arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()


def test_undo_restores_the_previous_version_and_again_reapplies_it(api, supabase, db_a):
    _, exam = start_builder(api, supabase, db_a)
    builder_tool(api, exam["id"], "generate_next_question", {**NULL_QUESTION, "cognitive_level": None})

    def prompt():
        exam_now = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()
        first = exam_now["versions"][0]["id"]
        return next(q for q in exam_now["questions"] if q["versionId"] == first and q["number"] == 1)["prompt"]

    original = prompt()
    revised = builder_tool(
        api,
        exam["id"],
        "revise_question",
        {"question_number": 1, "instruction": "Make it practical", "quick_action": "application"},
    )
    assert revised["ok"] and prompt() != original
    after_revision = prompt()

    undone = builder_tool(api, exam["id"], "undo_question_change", {"question_number": 1})
    assert undone["ok"] and undone["changedQuestionIds"] and prompt() == original
    assert undone["message"].startswith("Undid the last change to question 1 (") and "Make it practical" in undone["message"]
    redone = builder_tool(api, exam["id"], "undo_question_change", {"question_number": 1})
    assert redone["ok"] and prompt() == after_revision
    assert redone["message"] == "The last undo on question 1 was reversed, so its change is back."

    builder_tool(api, exam["id"], "approve_question", {"question_number": 1})
    locked = builder_tool(api, exam["id"], "undo_question_change", {"question_number": 1})
    assert locked["ok"] is False


def test_undo_explains_when_there_is_nothing_to_undo(api, supabase, db_a):
    _, exam = start_builder(api, supabase, db_a)
    # A freshly generated question has no earlier version.
    builder_tool(api, exam["id"], "generate_next_question", {**NULL_QUESTION, "cognitive_level": None})
    result = builder_tool(api, exam["id"], "undo_question_change", {"question_number": 1})
    assert result["ok"] is False and "nothing to undo" in result["error"]


def test_the_builder_knows_its_material_and_bloom_progress(api, supabase, db_a):
    _, exam = start_builder(api, supabase, db_a)
    builder_tool(api, exam["id"], "generate_next_question", {**NULL_QUESTION, "cognitive_level": None})
    state = builder_tool(api, exam["id"], "get_exam_state", {})["state"]
    assert state.startswith("Course material in use: ")
    assert "Bloom's " in state.split("Current split by marks:")[1]
