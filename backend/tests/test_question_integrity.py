"""Question integrity across an exam: no question gives away another's answer, marks follow the work,
question text stays clean, and demanding Bloom's/difficulty pairs get guidance (not silent changes).

Examples come from several subjects on purpose: the checks must hold beyond one lecture.
"""

import asyncio
from types import SimpleNamespace

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, reviewer_instructions
from app.schemas.ai import ChoiceOut, QuestionFix, ReviewIssue, ReviewOutput
from app.services.exam_review import _pairing_notes
from app.services.exam_store import answer_leaks, canonical_maths, question_problems
from tests.helpers import auth, wait_for_run
from tests.model import make_content, subpart, well_behaved_batch
from tests.seed import seed_assessment, seed_document
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def mcq(prompt: str, key: str, *distractors: str, number: int = 1):
    content = make_content(number, "mcq", "easy", 2)
    content.prompt = prompt
    content.choices = [ChoiceOut(id=letter, text=text) for letter, text in zip("ABCD", (key, *distractors), strict=False)]
    content.correct_choice = "A"
    return content


def written(prompt: str, number: int = 2):
    content = make_content(number, "short_answer", "medium", 4)
    content.prompt = prompt
    return content


SIGMOID = mcq(
    "Which expression defines the sigmoid function used in logistic regression?",
    r"\(g(z)=\frac{1}{1+e^{-z}}\)",
    r"\(g(z)=\frac{1}{1+e^{z}}\)",
    r"\(g(z)=z\)",
    r"\(g(z)=e^{-z}\)",
)


# -- Answer leakage: mathematics ---------------------------------------------------------------


def test_the_same_mathematics_in_any_notation_is_one_expression():
    forms = [r"\frac{1}{1+e^{-z}}", r"\(\dfrac{1}{1 + \exp(-z)}\)", "1/(1 + e^(−z))", r"\[ \tfrac{1}{1+e^{-z}} \]"]
    assert len({canonical_maths(form) for form in forms}) == 1
    assert canonical_maths(r"\frac{K(K-1)}{2}") == canonical_maths("K(K−1)/2")
    assert canonical_maths(r"\frac{1}{1+e^{z}}") != canonical_maths(r"\frac{1}{1+e^{-z}}")


def test_a_question_that_prints_another_questions_formula_answer_gives_it_away():
    # The v2 defect: Q4 printed the sigmoid that Q1 asked students to identify.
    for shown in (
        r"Use \[f(x)=\frac{1}{1+e^{-z}}\] with w = 2 and b = -6.",
        r"Use f(x) = 1/(1 + e^(−z)).",
        r"Use \(\dfrac{1}{1+\exp(-z)}\).",
    ):
        assert answer_leaks([SIGMOID, written(shown)]) == [(0, 1, r"\(g(z)=\frac{1}{1+e^{-z}}\)")], shown


def test_shared_formulas_and_text_matching_distractors_too_are_not_leaks():
    # Two problems may both give the sigmoid when no question asks students to recall it.
    assert (
        answer_leaks(
            [written(r"Use \(g(z)=\frac{1}{1+e^{-z}}\) for z = 2."), written(r"Given \(\frac{1}{1+e^{-z}}\), find z for 0.8.")]
        )
        == []
    )
    # A question showing the key next to a distractor (e.g. comparing them) doesn't single out the answer.
    compare = written(r"Compare \(\frac{1}{1+e^{-z}}\) with \(\frac{1}{1+e^{z}}\) as z grows.")
    assert answer_leaks([SIGMOID, compare]) == []
    # Short answers ('0.5', 'Class 1') appear in other questions by chance, and a question's own stem doesn't count.
    threshold = mcq("Which probability is the default threshold?", "0.5", "0.7", "0.9", "1.0")
    assert answer_leaks([threshold, written("Predict class 1 when f(x) >= 0.5.")]) == []
    assert answer_leaks([mcq(r"Is \(\frac{1}{1+e^{-z}}\) the sigmoid?", r"\(\frac{1}{1+e^{-z}}\)", "z", "e^z", "2z")]) == []


# -- Answer leakage: definitions in other subjects ---------------------------------------------


OSMOSIS = mcq(
    "Which statement describes osmosis?",
    "The movement of water molecules across a semi-permeable membrane from low to high solute concentration.",
    "The movement of solute particles from high to low concentration without a membrane.",
    "The active transport of water using ATP across a cell wall.",
    "The diffusion of gases through the walls of the lungs.",
)


def test_a_paraphrased_definition_in_another_question_gives_it_away():
    hint = written(
        "During osmosis, water molecules move across a semi-permeable membrane from a region of low solute "
        "concentration to one of high solute concentration. Explain why red blood cells swell in pure water."
    )
    assert [leak[:2] for leak in answer_leaks([OSMOSIS, hint])] == [(0, 1)]
    # Using the same vocabulary for another purpose is fine.
    use = written("Explain why a semi-permeable membrane matters in kidney dialysis, using the role of urea.")
    assert answer_leaks([OSMOSIS, use]) == []


def test_options_that_reorder_the_same_words_never_count_as_paraphrase_leaks():
    # Like v2's Q2: every option uses the same words, so word overlap can't single out the key.
    rest, pair = "one class against all remaining classes", "one class against another class"
    strategies = mcq(
        "Which description is correct?",
        f"One-versus-all trains {rest}; all-versus-all trains {pair}.",
        f"One-versus-all trains {pair}; all-versus-all trains {rest}.",
        f"One-versus-all trains {rest}; all-versus-all also trains {rest}.",
        f"One-versus-all trains {pair}; all-versus-all also trains {pair}.",
    )
    mention = written("A one-versus-all system trains one class against all remaining classes. How many classifiers for K = 4?")
    assert answer_leaks([strategies, mention]) == []


def test_history_and_law_style_answers_are_checked_the_same_way():
    treaty = mcq(
        "Which treaty ended the Thirty Years' War?",
        "The Peace of Westphalia, signed in 1648",
        "The Treaty of Utrecht, signed in 1713",
        "The Treaty of Versailles, signed in 1919",
        "The Congress of Vienna, concluded in 1815",
    )
    leaking = written("After the Peace of Westphalia, signed in 1648, how did state sovereignty change in Europe?")
    assert [leak[:2] for leak in answer_leaks([treaty, leaking])] == [(0, 1)]
    neutral = written("How did the end of the Thirty Years' War change state sovereignty in Europe?")
    assert answer_leaks([treaty, neutral]) == []


# -- Leaks are repaired during generation and reported (or fixed) by the quality check ----------


def test_generation_sends_back_a_batch_whose_question_gives_away_another_answer(api, supabase, db_a, ai):
    injected: list[int] = []

    def leaks_then_corrects(call):
        good = well_behaved_batch(call)
        bad = good.model_copy(deep=True)
        mcqs = [q for q in bad.questions if q.type == "mcq"]
        others = [q for q in bad.questions if q.type != "mcq" or (mcqs and q is not mcqs[0])]
        if not (mcqs and others):
            return good  # a batch without an MCQ and another question can't leak within itself
        key = next(choice.text for choice in mcqs[0].choices if choice.id == mcqs[0].correct_choice)
        others[0].prompt += f" Note: {key}."
        injected.append(len(ai.calls) - 1)
        return [bad, good]

    ai.on("generate_questions", leaks_then_corrects)
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    assert injected, "the plan must put an MCQ and another question in one batch"
    for index in injected:
        call = ai.calls[index]
        assert "gives away the answer to question" in " ".join(call["problems"][0]) and call["problems"][1] == []
    assert answer_leaks_in(exam) == []  # what was saved is clean


def answer_leaks_in(exam) -> list:
    from app.services.exam_store import row_to_content

    rows = [
        {**q, "correct_choice": q["correctChoice"], "estimated_minutes": q["estimatedMinutes"], "figure_document_id": None,
         "learning_objectives": q["learningObjectives"]}
        for q in sorted(exam["questions"], key=lambda q: q["number"])
    ]  # fmt: skip
    return answer_leaks([row_to_content(row) for row in rows])


def leaked_exam(api, supabase, db_a):
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    questions = sorted(exam["questions"], key=lambda q: q["number"])
    key_question = next(q for q in questions if q["type"] == "mcq")
    key = next(c["text"] for c in key_question["choices"] if c["id"] == key_question["correctChoice"])
    target = next(q for q in questions if q["id"] != key_question["id"])
    # A professor's edit may introduce a leak (manual edits are theirs to write); the check still sees it.
    leaked_prompt = f"{target['prompt']} Recall: {key}."
    assert api.patch(f"/api/questions/{target['id']}", json={"prompt": leaked_prompt}, headers=auth()).status_code == 200
    return exam, key_question, target, leaked_prompt


def review(api, exam_id):
    started = api.post(f"/api/exams/{exam_id}/review", headers=auth())
    assert wait_for_run(api, started.json()["run"]["id"])["status"] == "succeeded"
    return api.get(f"/api/exams/{exam_id}", headers=auth()).json()


def test_the_quality_check_reports_a_leak_and_tells_the_reviewer(api, supabase, db_a, ai):
    exam, key_question, target, _ = leaked_exam(api, supabase, db_a)
    reviewed = review(api, exam["id"])
    leaks = [issue for issue in reviewed["review"]["issues"] if issue["category"] == "answer_leakage"]
    assert len(leaks) == 1 and leaks[0]["questionId"] == target["id"] and leaks[0]["source"] == "check"
    assert f"gives away the answer to question {key_question['number']}" in leaks[0]["message"]
    assert reviewed["reviewStatus"] == "needs_attention"
    assert "FOUND BY CHECKS IN CODE" in ai.calls_for("review_exam")[-1]["input"]


def test_a_minimal_rewording_from_the_reviewer_removes_the_leak_and_the_warning(api, supabase, db_a, ai):
    exam, _, target, leaked_prompt = leaked_exam(api, supabase, db_a)
    fixed_prompt = leaked_prompt.split(" Recall: ")[0]

    def fixes_the_leak(_call):
        fix = QuestionFix(kind="clarify_wording", cognitive_level=None, correct_choice=None, prompt=fixed_prompt,
                          answer=None, solution=None, rubric=None)  # fmt: skip
        issue = ReviewIssue(question_number=target["number"], version_label="A", severity="warning",
                            category="answer_leakage", message="Remove the give-away.", fix=fix)  # fmt: skip
        return ReviewOutput(summary="One leak fixed.", issues=[issue], estimated_total_minutes=60)

    ai.on("review_exam", fixes_the_leak)
    reviewed = review(api, exam["id"])
    question = next(q for q in reviewed["questions"] if q["id"] == target["id"])
    assert question["prompt"] == fixed_prompt
    assert not [
        issue for issue in reviewed["review"]["issues"] if issue["category"] == "answer_leakage" and issue["source"] == "check"
    ]


# -- Demanding Bloom's/difficulty pairs: guidance, never a silent change -------------------------


def test_demanding_pairs_get_an_informational_note_and_the_setting_is_kept():
    rows = [
        {"id": "q1", "cognitive_level": "remember", "difficulty": "medium"},
        {"id": "q2", "cognitive_level": "understand", "difficulty": "hard"},
        {"id": "q3", "cognitive_level": "apply", "difficulty": "easy"},
        {"id": "q4", "cognitive_level": "analyze", "difficulty": "hard"},
    ]
    full = SimpleNamespace(primary_version={"id": "v", "label": "A"}, version_questions=lambda _id: rows)
    notes = _pairing_notes(full)  # type: ignore[arg-type]
    assert [(note["questionNumber"], note["severity"], note["category"]) for note in notes] == [
        (1, "info", "difficulty"),
        (2, "info", "difficulty"),
    ]
    assert (
        "closely related distinctions" in notes[0]["message"]
        and "without the stem supplying the interpretation" in notes[1]["message"]
    )
    assert all("kept as you chose it" in note["message"] for note in notes)


# -- Hygiene: marks and internal ids stay out of the text -----------------------------------------


def test_marks_written_into_question_text_are_sent_back():
    content = make_content(4, "problem", "easy", 6)
    content.subparts = [subpart("a", 3), subpart("b", 3)]
    content.prompt = "Use the data below. (6 marks)"
    assert any("writes marks into its text" in p for p in question_problems(content))
    content.prompt = "Use the data below."
    content.subparts[1].prompt = "Compute the variance. [3 pts]"
    assert any("writes marks into its text" in p for p in question_problems(content))
    # Ordinary uses of 'points' are not marks; a professor's own edit is theirs to write.
    content.subparts[1].prompt = "A team scored 3 points in each of 4 games. Compute the variance."
    assert not any("writes marks" in p for p in question_problems(content))
    content.prompt = "Use the data below. (6 marks)"
    assert not any("writes marks" in p for p in question_problems(content, manual=True))


def test_internal_ids_in_concepts_are_sent_back():
    content = make_content(1, "mcq", "easy", 2)
    content.concepts = ["Sigmoid function", "Logistic Regression.pdf, pages 10–12; topic L1.t3"]
    assert any("concepts or learning objectives contain internal ids" in p for p in question_problems(content))
    content.concepts = ["Sigmoid function"]
    assert not any("internal ids" in p for p in question_problems(content))


# -- Guidance for marks, leaks and demanding pairs reaches every step -----------------------------


def test_planner_writer_and_reviewer_rules():
    planner, writer, reviewer = (f(DataFramer()) for f in (planner_instructions, generator_instructions, reviewer_instructions))
    assert "no other question may state it" in planner and "POINTS follow the work" in planner
    assert "Never give away another question's answer" in writer and "Formulas no other question asks for may be given" in writer
    assert (
        "every rubric criterion rewards a distinct, non-trivial step" in writer
        and "earns only the marks of the reasoning" in writer
    )
    assert "Don't write marks into the prompt" in writer and "never sources or ids" in writer
    assert "category 'answer_leakage'" in reviewer and "category 'marks'" in reviewer
    for text in (planner, writer, reviewer):  # the shared difficulty guide
        assert "a hard Understand question asks students to explain an unfamiliar case" in text
    assert {"answer_leakage", "marks"} <= set(ReviewIssue.model_fields["category"].annotation.__args__)
