"""Latency: each operation does only what it needs, independent work runs concurrently,
and Build with AI reports a tool the moment it starts. OpenAI is faked, with delays."""

import asyncio
import json
import time
from collections import Counter

import pytest

from app.api.exams import _chat_events
from app.services.exam_builder import ExamBuilderService
from tests.fakes import FakeAI
from tests.helpers import auth
from tests.model import install_well_behaved
from tests.seed import seed_assessment, seed_document
from tests.test_builder import NULL_QUESTION, builder_script, start_builder
from tests.test_planning import grouped_plan, use_plan
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve

# Work that a single-question operation must never trigger.
HEAVY = {"summarize_document", "analyze_previous_exams", "interpret_assessment", "plan_exam", "replan_exam"}
HEAVY |= {"generate_questions", "write_variants", "review_exam"}


class TimedAI(FakeAI):
    """The fake model, with a delay per purpose, recording how many calls overlap."""

    def __init__(self) -> None:
        super().__init__()
        self.delays: dict[str, float] = {"generate_questions": 0.05, "write_variants": 0.05, "review_exam": 0.01}
        self.in_flight: Counter[str] = Counter()
        self.peak: Counter[str] = Counter()
        self.spans: list[tuple[str, float, float]] = []
        self.gates: dict[str, asyncio.Event] = {}

    async def parse(self, *, purpose, **kwargs):
        self.in_flight[purpose] += 1
        self.peak[purpose] = max(self.peak[purpose], self.in_flight[purpose])
        started = time.monotonic()
        try:
            if purpose in self.gates:
                await self.gates[purpose].wait()
            await asyncio.sleep(self.delays.get(purpose, 0))
            return await super().parse(purpose=purpose, **kwargs)
        finally:
            self.in_flight[purpose] -= 1
            self.spans.append((purpose, started, time.monotonic()))


@pytest.fixture
def ai() -> TimedAI:
    return install_well_behaved(TimedAI())


def reset(ai: FakeAI, supabase) -> None:
    ai.calls.clear()
    ai.embedding_calls = 0
    supabase.rpc_calls.clear()


def ready_exam(api, supabase, db_a, **settings) -> tuple[dict, dict]:
    assessment = asyncio.run(seed_assessment(db_a, **{**A_SETTINGS, **settings}))
    asyncio.run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    return assessment, generate(api, assessment["id"])


def test_full_generation_writes_batches_concurrently_and_reviews_once(api, supabase, db_a, ai):
    _, exam = ready_exam(api, supabase, db_a)
    batches = ai.calls_for("generate_questions")
    # The plan is made once, the batches overlap (bounded), and the full review runs once, at the end.
    assert len(ai.calls_for("plan_exam")) == 1
    assert 2 <= ai.peak["generate_questions"] <= 3 and len(batches) == 3
    assert len(ai.calls_for("review_exam")) == 1
    last_batch = max(end for purpose, _, end in ai.spans if purpose == "generate_questions")
    review_start = min(start for purpose, start, _ in ai.spans if purpose == "review_exam")
    assert review_start >= last_batch
    # High effort only for batches with hard or multi-step questions.
    assert sorted(call["effort"] for call in batches) == ["high", "high", "medium"]
    assert len(exam["questions"]) == 20  # 10 questions in each of 2 versions


def test_every_version_is_written_at_once(api, supabase, db_a, ai):
    use_plan(ai, grouped_plan().model_copy(update={"questions": grouped_plan().questions[:4]}))
    ready_exam(api, supabase, db_a, number_of_versions=3)
    # One variant batch per version (B and C), written at the same time rather than one version after another.
    assert len(ai.calls_for("write_variants")) == 2 and ai.peak["write_variants"] == 2


def test_revising_one_question_does_only_that(api, supabase, db_a, ai):
    _, exam = ready_exam(api, supabase, db_a)
    first = exam["versions"][0]["id"]
    questions = sorted((q for q in exam["questions"] if q["versionId"] == first), key=lambda q: q["number"])
    target = next(q for q in questions if q["difficulty"] == "medium" and q["type"] == "mcq")
    reviewed_at = exam["reviewedAt"]
    reset(ai, supabase)

    response = api.post(f"/api/questions/{target['id']}/revision", json={"preset": "clearer"}, headers=auth())
    assert response.status_code == 200, response.text

    # One reasoning call at medium effort, one search: no file reading, style analysis,
    # planning, regeneration or exam review.
    assert [call["purpose"] for call in ai.calls] == ["revise_question"]
    assert ai.calls[0]["effort"] == "medium"
    assert not HEAVY & {call["purpose"] for call in ai.calls}
    assert ai.embedding_calls == 1 and set(supabase.rpc_calls) == {"match_document_chunks"}
    after = api.get(f"/api/exams/{exam['id']}", headers=auth()).json()
    unchanged = {q["id"]: q["prompt"] for q in after["questions"] if q["id"] != target["id"]}
    assert unchanged == {q["id"]: q["prompt"] for q in exam["questions"] if q["id"] != target["id"]}
    assert after["reviewedAt"] == reviewed_at


def test_a_hard_multi_step_revision_keeps_high_effort(api, supabase, db_a, ai):
    _, exam = ready_exam(api, supabase, db_a)
    problem = next(q for q in exam["questions"] if q["type"] == "problem" and q["difficulty"] == "medium")
    reset(ai, supabase)
    api.post(f"/api/questions/{problem['id']}/revision", json={"preset": "harder"}, headers=auth())
    assert [(call["purpose"], call["effort"]) for call in ai.calls] == [("revise_question", "high")]


def test_generating_the_next_question_does_only_that(api, supabase, db_a, ai):
    _, exam = start_builder(api, supabase, db_a)
    reset(ai, supabase)
    response = api.post(
        f"/api/exams/{exam['id']}/builder/tools/generate_next_question", json={"arguments": NULL_QUESTION}, headers=auth()
    )
    assert response.json()["ok"] is True
    assert [call["purpose"] for call in ai.calls] == ["generate_question"]
    assert ai.embedding_calls == 1 and supabase.rpc_calls == ["match_document_chunks"]


def test_the_chat_reports_a_tool_before_the_ai_returns(api, supabase, db_a, ai):
    ai.tool_scripts["builder_chat"] = builder_script
    _, exam = start_builder(api, supabase, db_a)

    async def scenario() -> list[dict]:
        ai.gates["generate_question"] = asyncio.Event()
        events = _chat_events(ExamBuilderService(db_a, ai), exam["id"], "Let's start with the first question.")
        first = json.loads(await anext(events))
        # The status is out as the tool starts, before the model writes anything...
        assert first == {"event": "tool", "name": "generate_next_question", "arguments": NULL_QUESTION}
        await asyncio.sleep(0.05)
        # ...and the (fake) model is still writing: nothing is saved yet.
        assert ai.in_flight["generate_question"] == 1
        assert not [q for q in supabase.tables["exam_questions"] if q["exam_id"] == exam["id"]]
        ai.gates["generate_question"].set()
        return [first, *[json.loads(line) async for line in events]]

    events = asyncio.run(scenario())
    done = events[-1]
    assert [event["event"] for event in events] == ["tool", "done"]
    saved = [q for q in supabase.tables["exam_questions"] if q["exam_id"] == exam["id"]]
    assert len(saved) == 1 and done["changedQuestionIds"] == [saved[0]["id"]] and done["reply"]


def test_the_chat_streams_when_asked_and_answers_as_before_otherwise(api, supabase, db_a, ai):
    ai.tool_scripts["builder_chat"] = builder_script
    _, exam = start_builder(api, supabase, db_a)
    url = f"/api/exams/{exam['id']}/builder/messages"
    streamed = api.post(
        url, json={"content": "Let's start with the first question."}, headers={**auth(), "Accept": "application/x-ndjson"}
    )
    assert streamed.status_code == 200 and streamed.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in streamed.text.splitlines()]
    assert [line["event"] for line in lines] == ["tool", "done"] and lines[1]["changedQuestionIds"]
    plain = api.post(url, json={"content": "Make it more practical."}, headers=auth())
    assert plain.headers["content-type"].startswith("application/json") and plain.json()["changedQuestionIds"]
    # A request that can't start is still a normal error response, even when streaming.
    empty = api.post(url, json={"content": "   "}, headers={**auth(), "Accept": "application/x-ndjson"})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "invalid_input"


def batch(rows: list[tuple[str, str]]) -> list:
    from app.schemas.ai import PlannedQuestion

    return [
        PlannedQuestion(
            number=number,
            section_index=0,
            type=kind,
            difficulty=difficulty,
            points=10,
            topic=f"topic {number}",
            learning_objective="x",
            cognitive_level="application",
            source_hint=None,
            estimated_minutes=5,
            figure_document_id=None,
        )
        for number, (kind, difficulty) in enumerate(rows, start=1)
    ]


@pytest.mark.parametrize(
    ("rows", "parallel", "sizes"),
    [
        # 6 hard or multi-step questions and 4 others: two high-effort batches of 3, one medium batch of 4.
        ([("problem", "medium")] * 3 + [("problem", "hard")] * 3 + [("mcq", "easy")] * 4, 3, [3, 3, 4]),
        ([("mcq", "easy")] * 6, 3, [2, 2, 2]),  # small exams still use every parallel slot
        ([("mcq", "medium")] * 2, 3, [1, 1]),
        ([("short_answer", "medium")] * 14, 3, [3, 3, 2, 2, 2, 2]),  # two full rounds, no batch over 4
        ([("problem", "hard")] * 8, 3, [3, 3, 2]),
    ],
)
def test_batches_are_balanced_and_bounded(rows, parallel, sizes):
    from app.services.exam_generation import _BATCH_SIZE, _batches, batch_effort

    planned = batch(rows)
    batches = _batches(planned, parallel)
    assert [len(part) for part in batches] == sizes and max(sizes) <= _BATCH_SIZE
    assert sorted(q.number for part in batches for q in part) == [q.number for q in planned]
    efforts = [batch_effort(part) for part in batches]
    assert efforts == sorted(efforts, key=lambda effort: effort != "high")  # slowest first
