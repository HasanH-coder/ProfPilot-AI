"""Scenario G (Build with AI), the setup assistant's chat, and voice credentials."""

import asyncio

from tests.helpers import auth, wait_for_run
from tests.seed import seed_assessment, seed_course, seed_document
from tests.test_workflow import A_SETTINGS, interpret_and_approve

NULL_QUESTION = {"instructions": None, "question_type": None, "difficulty": None, "topic": None, "points": None}


def run(coroutine):
    return asyncio.run(coroutine)


def builder_script(messages):
    """A scripted conversational model: maps what the professor typed to tool calls."""
    text = messages[-1]["content"].lower()
    if "first question" in text:
        return [("generate_next_question", NULL_QUESTION)], "Here's question 1. It tests applying gradient descent."
    if "more practical" in text:
        return (
            [("revise_question", {"question_number": 1, "instruction": messages[-1]["content"], "quick_action": "application"})],
            "I made question 1 more practical.",
        )
    if "keep it" in text and "next" in text:
        return (
            [
                ("approve_question", {"question_number": 1}),
                ("generate_next_question", {**NULL_QUESTION, "instructions": "a practical problem"}),
            ],
            "Question 1 is approved. Here's question 2.",
        )
    if "question 9" in text:
        return [("revise_question", {"question_number": 9, "instruction": "x", "quick_action": None})], "Hmm."
    return [], "Okay."


def start_builder(api, supabase, db_a) -> tuple[dict, dict]:
    assessment = run(seed_assessment(db_a, **A_SETTINGS))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    created = api.post(f"/api/assessments/{assessment['id']}/exam", json={"mode": "interactive"}, headers=auth())
    assert created.status_code == 202, created.text
    exam = created.json()["exam"]
    assert exam["status"] == "building" and exam["questions"] == [] and len(exam["versions"]) == 2
    return assessment, exam


def say(api, exam_id: str, content: str) -> dict:
    response = api.post(f"/api/exams/{exam_id}/builder/messages", json={"content": content}, headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


def questions_a(api, exam_id: str) -> list[dict]:
    exam = api.get(f"/api/exams/{exam_id}", headers=auth()).json()
    first = exam["versions"][0]["id"]
    return sorted((q for q in exam["questions"] if q["versionId"] == first), key=lambda q: q["number"])


def test_scenario_g_build_question_by_question(api, supabase, db_a, ai):
    ai.tool_scripts["builder_chat"] = builder_script
    _, exam = start_builder(api, supabase, db_a)

    first = say(api, exam["id"], "Let's start with the first question.")
    q1 = questions_a(api, exam["id"])
    assert len(q1) == 1 and first["changedQuestionIds"] == [q1[0]["id"]]
    # The conversational model only chose the tool; the reasoning model wrote the question,
    # with medium effort for a normal (non-hard) question.
    written = ai.calls_for("generate_question")[0]
    assert q1[0]["difficulty"] != "hard" and written["effort"] == "medium"

    revised = say(api, exam["id"], "Make it more practical.")
    after = questions_a(api, exam["id"])
    assert revised["changedQuestionIds"] == [q1[0]["id"]] and after[0]["prompt"] != q1[0]["prompt"]

    say(api, exam["id"], "Perfect, keep it and go to the next one.")
    final = questions_a(api, exam["id"])
    assert final[0]["status"] == "approved" and len(final) == 2

    # The conversation is kept, so a reload shows it.
    messages = api.get(f"/api/exams/{exam['id']}/builder/messages", headers=auth()).json()
    assert [m["role"] for m in messages][:2] == ["professor", "assistant"] and len(messages) == 6

    # A tool error is reported back to the model instead of crashing the chat.
    say(api, exam["id"], "Change question 9.")
    result = ai.calls[-1]["tool_results"][0]
    assert result["ok"] is False and "There is no question 9" in result["error"]


def test_voice_tools_use_the_same_executor(api, supabase, db_a):
    _, exam = start_builder(api, supabase, db_a)
    generated = api.post(
        f"/api/exams/{exam['id']}/builder/tools/generate_next_question", json={"arguments": NULL_QUESTION}, headers=auth()
    ).json()
    assert generated["ok"] is True and generated["question"]["number"] == 1
    state = api.post(f"/api/exams/{exam['id']}/builder/tools/get_exam_state", json={"arguments": {}}, headers=auth()).json()
    assert "1. [" in state["state"] and "Suggested next" in state["state"]
    unknown = api.post(f"/api/exams/{exam['id']}/builder/tools/drop_database", json={"arguments": {}}, headers=auth())
    assert unknown.status_code == 422
    transcript = api.post(
        f"/api/exams/{exam['id']}/builder/transcript", json={"role": "professor", "content": "Make it harder."}, headers=auth()
    ).json()
    assert transcript["channel"] == "voice"

    finished = api.post(f"/api/exams/{exam['id']}/builder/tools/finish_exam", json={"arguments": {}}, headers=auth()).json()
    assert finished["ok"] is True
    assert wait_for_run(api, finished["runId"])["status"] == "succeeded"
    done = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()
    assert done["status"] == "ready"
    assert [v["summary"]["questionCount"] for v in done["versions"]] == [1, 1]  # Version B created
    # One question can't meet a 40/60 format and 30/40/30 difficulty target: the
    # quality check says so (and only that; nothing is structurally wrong).
    assert done["reviewStatus"] == "needs_attention"
    assert {issue["category"] for issue in done["review"]["issues"]} == {"format", "difficulty"}


def test_setup_assistant_text_chat_fills_the_form(api, db_a, ai):
    course = run(seed_course(db_a))

    def script(messages):
        return (
            [
                ("set_duration", {"minutes": 90}),
                ("set_difficulty_distribution", {"easy_percent": 30, "medium_percent": 40, "hard_percent": 30}),
                ("set_versions", {"count": 2}),
                ("set_course", {"course_id": course["id"]}),
                ("update_notes", {"text": "Focus more on application.", "mode": "append"}),
                ("set_question_distribution", {"mcq_percent": 130}),
            ],
            "Done: 90 minutes, 30/40/30, two versions.",
        )

    ai.tool_scripts["setup_chat"] = script
    response = api.post(
        "/api/setup-assistant/messages",
        headers=auth(),
        json={
            "setup": {"durationMinutes": None, "numberOfVersions": 1},
            "messages": [{"role": "professor", "content": "Around 90 minutes, 30 easy 40 medium 30 hard, two versions."}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    setup = body["setup"]
    assert (setup["durationMinutes"], setup["easyPercentage"], setup["mediumPercentage"], setup["hardPercentage"]) == (
        90,
        30,
        40,
        30,
    )
    assert setup["numberOfVersions"] == 2 and setup["courseId"] == course["id"]
    assert setup["additionalNotes"] == "Focus more on application."
    assert setup["mcqPercentage"] is None  # the invalid 130% was rejected, not applied
    assert ai.calls[-1]["tool_results"][-1]["ok"] is False


def test_setup_chat_skips_a_setting_and_moves_to_the_next_one(api, db_a, ai):
    run(seed_course(db_a))
    ai.tool_scripts["setup_chat"] = lambda messages: (
        [("skip_setting", {"setting": "duration"})],
        "Okay. Do you want to specify the question format?",
    )
    response = api.post(
        "/api/setup-assistant/messages",
        headers=auth(),
        json={
            "setup": {"examName": "Midterm"},
            "addressed": ["course"],
            "messages": [{"role": "professor", "content": "Skip the duration."}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Skipped means left empty, and remembered so it isn't asked again.
    assert body["setup"]["durationMinutes"] is None and body["changedFields"] == []
    assert body["addressed"] == ["course", "duration"]
    call = ai.calls[-1]
    assert (
        "NEXT QUESTION, if the professor's message doesn't change the topic: Do you want to specify a duration?"
        in (call["instructions"])
    )
    assert call["tool_results"][0]["nextQuestion"] == "Do you want to specify the question format?"


def test_a_voice_setup_call_opens_with_one_question(api, db_a, ai):
    run(seed_course(db_a))
    response = api.post(
        "/api/realtime/client-secrets",
        headers=auth(),
        json={"purpose": "setup", "setup": {"examName": "Midterm"}, "addressed": ["course"]},
    )
    assert response.status_code == 200, response.text
    instructions = ai.secrets[-1]["session"]["instructions"]
    assert "Open with a short greeting and the first question only, e.g. 'Hi. Do you want to specify a duration?'" in (
        instructions
    )
    assert "ONE QUESTION AT A TIME" in instructions


def test_setup_tool_endpoint_for_voice_calls(api, db_a):
    response = api.post(
        "/api/setup-assistant/tools/set_difficulty_distribution",
        headers=auth(),
        json={
            "setup": {},
            "arguments": {"easy_percent": 30, "medium_percent": 30, "hard_percent": 30},
        },
    )
    # Not applied and not normalized: the assistant is told to ask about the missing 10%.
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "adds up to 90%" in message and "remaining 10%" in message and "Don't change their numbers" in message
    ok = api.post("/api/setup-assistant/tools/set_duration", headers=auth(), json={"setup": {}, "arguments": {"minutes": 90}})
    assert ok.json()["setup"]["durationMinutes"] == 90
    assert ok.json()["result"]["addressed"] == ["duration"]


def test_voice_credentials_are_short_lived_and_configured_on_the_server(api, db_a, ai):
    run(seed_course(db_a))
    response = api.post(
        "/api/realtime/client-secrets", headers=auth(), json={"purpose": "setup", "setup": {"durationMinutes": 60}}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"clientSecret", "expiresAt", "model"}  # nothing else reaches the browser
    assert body["clientSecret"].startswith("ek_") and body["model"] == "gpt-realtime-2.1"
    minted = ai.secrets[-1]
    session = minted["session"]
    assert minted["ttl"] <= 120 and session["type"] == "realtime" and session["model"] == "gpt-realtime-2.1"
    assert "Duration: 60 minutes" in session["instructions"] and "CMPS 297U" in session["instructions"]
    names = {tool["name"] for tool in session["tools"]}
    assert {"set_duration", "set_difficulty_distribution", "finish_setup"} <= names
    assert all("strict" not in tool for tool in session["tools"])
    assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert len(minted["safety_identifier"]) == 64  # hashed, not the professor's id


def test_builder_voice_credentials_need_an_interactive_exam_you_own(api, supabase, db_a):
    _, exam = start_builder(api, supabase, db_a)
    ok = api.post("/api/realtime/client-secrets", headers=auth(), json={"purpose": "builder", "examId": exam["id"]})
    assert ok.status_code == 200
    stranger = api.post(
        "/api/realtime/client-secrets", headers=auth("token-b"), json={"purpose": "builder", "examId": exam["id"]}
    )
    assert stranger.status_code == 404


def test_voice_credentials_are_rate_limited(api, db_a):
    statuses = [
        api.post("/api/realtime/client-secrets", headers=auth(), json={"purpose": "setup"}).status_code for _ in range(13)
    ]
    assert statuses[:12] == [200] * 12 and statuses[12] == 400
