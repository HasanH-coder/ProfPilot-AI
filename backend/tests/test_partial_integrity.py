"""Academic integrity beyond full give-aways: partial clues between questions, safe reviewer fixes to
question parts, distinct assessment targets, and formula warnings the professor's own formulas settle.

Examples come from several subjects on purpose (statistics, chemistry, economics, physics, machine
learning): the checks must hold beyond one lecture. Adversarial cases check what must NOT be flagged
or changed as carefully as what must.
"""

import asyncio
import copy

from app.ai.prompts import DataFramer, generator_instructions, planner_instructions, reviewer_instructions
from app.schemas.ai import ExamPlan, PlannedSection, QuestionFix, ReviewIssue, ReviewOutput, RubricItem, SubpartOut
from app.services.exam_generation import plan_problems
from app.services.exam_store import answer_leaks, integrity_leaks, partial_leaks, rewording_keeps_maths
from app.services.lecture_knowledge import professor_formula_text, traced_to_professor
from tests.helpers import auth
from tests.model import well_behaved_batch
from tests.seed import seed_assessment, seed_document
from tests.test_bloom import slot, spec_with
from tests.test_formula_fidelity import plan_on_first_topic, with_garbled_formulas
from tests.test_question_integrity import SIGMOID, mcq, review, snapshot, written
from tests.test_workflow import A_SETTINGS, generate, interpret_and_approve


def run(coroutine):
    return asyncio.run(coroutine)


def worked(prompt: str, answer: str, number: int = 2):
    content = written(prompt, number)
    content.answer = answer
    return content


# -- Partial leaks: results shown elsewhere -------------------------------------------------------


LOSSES = worked(
    r"Two containers have model outputs \(f=0.64\) and \(f=0.88\), and both true labels are \(y=0\). Compute each loss.",
    r"\(L_A=-\ln(0.36)=1.022\) and \(L_B=-\ln(0.12)=2.120\).",
)


def test_a_lookup_table_that_prints_another_questions_result_is_a_partial_leak():
    table = written("Use these values.\n\n| Expression | Value |\n| --- | --- |\n| \\(\\ln(0.36)\\) | \\(-1.0217\\) |")
    assert partial_leaks([LOSSES, table]) == [(0, 1, "result", "1.022")]
    # The v3 table (other values) helps no other question.
    v3_table = written("| \\(e^{-0.8}\\) | \\(0.449328964117\\) |\n| \\(\\ln(0.72)\\) | \\(-0.328504066972\\) |")
    assert integrity_leaks([LOSSES, v3_table]) == []


def test_a_given_value_that_is_another_questions_result_is_a_partial_leak_in_chemistry():
    molar = worked(
        "Calculate the molar mass of sodium chloride from Na = 22.99 g/mol and Cl = 35.45 g/mol.",
        "M = 22.99 + 35.45 = 58.44 g/mol.",
    )
    moles = written("A solution contains 11.69 g of NaCl (molar mass 58.44 g/mol). Find the number of moles.", 3)
    assert partial_leaks([molar, moles]) == [(0, 1, "result", "58.44")]
    # Data both questions are given is shared context, not a leak.
    shared = written("Sodium has a molar mass of 22.99 g/mol. Explain why it forms a +1 ion.", 3)
    assert partial_leaks([molar, shared]) == []


def test_integers_and_short_decimals_recur_by_chance_and_never_count():
    counts = worked("How many classifiers does all-versus-all train for K = 10, and what is the threshold?", "45 and 0.5.")
    other = written("A survey of 45 students used a 0.5 cut-off. Compute the mean of 1.5, 2.5 and 3.5.", 3)
    assert partial_leaks([counts, other]) == []


def test_an_mcq_key_value_shown_elsewhere_singles_out_the_option():
    loss = mcq("What is the loss for f = 0.72 and y = 1?", "0.329", "0.511", "1.273", "0.693")
    hint = written(r"Note that \(-\ln(0.72)=0.3285\).", 2)
    assert partial_leaks([loss, hint]) == [(0, 1, "result", "0.329")]
    # A distractor's value singles out nothing.
    assert partial_leaks([loss, written(r"Note that \(-\ln(0.6)=0.511\).", 2)]) == []


# -- Partial leaks: the correct formula in another notation --------------------------------------


def test_the_key_formula_with_a_renamed_variable_still_gives_it_away():
    assert [leak[:2] for leak in answer_leaks([SIGMOID, written(r"Evaluate \(\frac{1}{1+e^{-t}}\) at t = 2.")])] == [(0, 1)]


def test_an_instance_that_fits_a_distractor_too_singles_out_nothing():
    # 1/(1+e^{-0.8}) is the key at z = 0.8 and distractor 1/(1+e^{z}) at z = -0.8.
    assert answer_leaks([SIGMOID, written(r"Evaluate \(\frac{1}{1+e^{-0.8}}\).")]) == []
    # The v3 lookup value e^{-0.8} alone rules out one distractor at most: left to the reviewer.
    assert integrity_leaks([SIGMOID, written(r"Given \(e^{-0.8}=0.4493\), compute \(g(0.8)\).")]) == []


# -- Partial leaks: an explanation another question asks for -------------------------------------


CEILING = worked(
    "Explain why a binding price ceiling causes a shortage.",
    "Below the equilibrium price, quantity demanded exceeds quantity supplied because buyers want more at the "
    "lower price while sellers offer less, so a persistent shortage results.",
)


def test_supplying_the_reasoning_another_question_asks_for_is_a_partial_leak_in_economics():
    leaking = written(
        "A rent ceiling below the equilibrium price means quantity demanded exceeds quantity supplied because buyers "
        "want more at the lower price while sellers offer less, so a persistent shortage results. Compute the "
        "shortage if Qd = 500 and Qs = 300.",
        3,
    )
    leaks = partial_leaks([CEILING, leaking])
    assert [leak[:3] for leak in leaks] == [(0, 1, "explanation")] and "quantity demanded exceeds" in leaks[0][3]
    neutral = written(
        "A city caps rents at $900 a month, below the market rate. Compute the shortage if Qd = 500 and Qs = 300.", 3
    )
    assert partial_leaks([CEILING, neutral]) == []


def test_prerequisite_information_and_shared_context_are_not_leaks():
    median = worked(
        "Explain why the median is preferred for skewed income data.",
        "The median depends only on the middle observation's position, so a few extremely large incomes leave it "
        "unchanged while they pull the mean upward.",
    )
    task = written("The incomes below are right-skewed. Compute the mean and the median, then compare them.", 3)
    assert integrity_leaks([median, task]) == []


def test_the_v3_exam_has_no_deterministic_leak_its_cues_need_the_reviewer():
    """Q4's lookup value e^{-0.8} and 'predicted class-1 probability' wording were judged by the AI review:
    each only partly hints at Q1 or Q3, so the code checks must not call them give-aways."""
    q3 = worked(
        r"Two containers have outputs \(f(x)=0.64\) and \(0.88\) with true label \(y=0\). Explain how the sorting "
        "decisions and per-example losses follow. Numerical loss calculations are not required.",
        r"Each output is the estimated probability of class \(1\). Both exceed \(0.5\), so both are predicted class "
        r"\(1\). The loss uses the true label, through \(-\ln(1-f)\): B gets the larger loss.",
        3,
    )
    q4 = written(
        r"The parts are independent. Lookup values: \(e^{-0.8}=0.449328964117\), \(\ln(0.72)=-0.328504066972\). "
        r"(c) For another example, \(y=0\) and the predicted class-\(1\) probability is \(f=0.28\).",
        4,
    )
    assert integrity_leaks([SIGMOID, q3, q4]) == []


# -- Rewordings keep the mathematics -------------------------------------------------------------


def test_a_rewording_may_change_words_but_not_mathematics():
    before = written(r"For \(y=0\), the predicted class-\(1\) probability is \(f=0.28\). Use \(\ln(0.72)=-0.3285\).")
    before.answer = r"\(L=-\ln(0.72)=0.3285\)"

    def reworded(text):
        after = before.model_copy(deep=True)
        after.prompt = text
        return after

    # Words only (the class label '1' is not mathematics): kept.
    assert rewording_keeps_maths(
        before, reworded(r"For \(y=0\), the model output is \(f=0.28\). Use \(\ln(0.72)=-0.3285\)."), removes_leak=False
    )
    # A changed value, or a new one, is never accepted.
    assert not rewording_keeps_maths(
        before, reworded(r"For \(y=0\), the probability is \(f=0.30\). Use \(\ln(0.72)=-0.3285\)."), removes_leak=True
    )
    assert not rewording_keeps_maths(before, reworded(before.prompt + " Recall 0.25."), removes_leak=True)
    # Removing a value the answer key works with leaves students without it, even to remove a leak.
    assert not rewording_keeps_maths(before, reworded(r"For \(y=0\), the probability is \(f=0.28\)."), removes_leak=True)
    # Removing a value the key doesn't use is accepted only when it removes a give-away.
    hinted = reworded(before.prompt + r" Note \(-\ln(0.36)=1.0217\).")
    assert rewording_keeps_maths(hinted, reworded(before.prompt), removes_leak=True)
    assert not rewording_keeps_maths(hinted, reworded(before.prompt), removes_leak=False)


# -- Reviewer fixes to one subpart ---------------------------------------------------------------

PART_B = (
    r"A screening model is checked on one more example, unrelated to part (a). For \(y=0\), the predicted "
    r"class-\(1\) probability is \(f=0.28\); use \(\ln(0.72)=-0.3285\). Calculate the per-example loss."
)


def exam_with_parts(api, supabase, db_a):
    """A generated exam in which one written question has two parts with their own answers and rubrics."""
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    target = next(q for q in sorted(exam["questions"], key=lambda q: q["number"]) if q["type"] != "mcq")
    half = target["points"] / 2
    parts = [
        {
            "label": "a",
            "prompt": r"Calculate the linear score \(z\) for \(w=(0.6,-0.8)\), \(x=(1.5,2.5)\) and \(b=0.4\).",
            "points": half,
            "answer": r"\(z=0.9-2.0+0.4=-0.700\)",
            "rubric": [{"criterion": "Substitutes both products and the bias.", "points": half}],
        },
        {
            "label": "b",
            "prompt": PART_B,
            "points": half,
            "answer": r"\(L=-\ln(1-0.28)=-\ln(0.72)=0.3285\)",
            "rubric": [
                {"criterion": "Uses the target-zero loss.", "points": half / 2},
                {"criterion": "Correct value.", "points": half / 2},
            ],
        },
    ]
    run(
        db_a.update(
            "exam_questions",
            {"subparts": parts, "rubric": None, "answer": "See the parts.", "prompt": "The two parts are independent."},
            filters=[("id", "eq", target["id"])],
        )
    )
    return api.get(f"/api/exams/{exam['id']}", headers=auth()).json(), target


def proposing(number: int, fix: QuestionFix):
    issue = ReviewIssue(question_number=number, version_label="A", severity="warning", category="answer_leakage",
                        message="Part (b) hints at another question.", fix=fix)  # fmt: skip
    return lambda _call: ReviewOutput(summary="One fix.", issues=[issue], estimated_total_minutes=60)


def wording(prompt: str, label: str | None = "b", kind: str = "clarify_wording") -> QuestionFix:
    return QuestionFix(kind=kind, cognitive_level=None, correct_choice=None, prompt=prompt, answer=None,
                       solution=None, rubric=None, subpart_label=label)  # fmt: skip


def rubric_fix(label: str, *points: float) -> QuestionFix:
    rubric = [RubricItem(criterion=f"Step {index + 1}", points=value) for index, value in enumerate(points)]
    return QuestionFix(kind="fix_rubric", cognitive_level=None, correct_choice=None, prompt=None, answer=None,
                       solution=None, rubric=rubric, subpart_label=label)  # fmt: skip


def revisions(api, question_id):
    return api.get(f"/api/questions/{question_id}/revisions", headers=auth()).json()


def test_the_reviewer_can_reword_one_part_and_nothing_else_changes(api, supabase, db_a, ai):
    exam, target = exam_with_parts(api, supabase, db_a)
    before = snapshot(exam)
    neutral = PART_B.replace(r"the predicted class-\(1\) probability", "the model output")
    ai.on("review_exam", proposing(target["number"], wording(neutral, "(B)")))  # labels match however they're written
    reviewed = review(api, exam["id"])

    after = snapshot(reviewed)
    assert [qid for qid in before if before[qid] != after[qid]] == [target["id"]]  # no other question changed
    old, new = before[target["id"]], after[target["id"]]
    assert {key for key in old if old[key] != new[key]} == {"subparts"}  # prompt, key, rubric, level, points kept
    assert new["subparts"][0] == old["subparts"][0]
    assert new["subparts"][1] == {**old["subparts"][1], "prompt": neutral}  # its answer, rubric and points kept
    question = next(q for q in reviewed["questions"] if q["id"] == target["id"])
    assert question["sources"] == next(q for q in exam["questions"] if q["id"] == target["id"])["sources"]
    assert [r["source"] for r in revisions(api, target["id"])] == ["ai_review_fix"]
    assert next(i for i in reviewed["review"]["issues"] if i["source"] == "ai")["fixApplied"] is True


def test_unsafe_part_fixes_are_rejected_without_any_change_or_revision(api, supabase, db_a, ai):
    exam, target = exam_with_parts(api, supabase, db_a)
    before = snapshot(exam)
    other = next(q for q in exam["questions"] if q["type"] != "mcq" and q["id"] != target["id"])
    explanation = "Confident wrong predictions receive heavy penalties under logarithmic loss."
    run(db_a.update("exam_questions", {"answer": explanation}, filters=[("id", "eq", other["id"])]))
    before = snapshot(api.get(f"/api/exams/{exam['id']}", headers=auth()).json())
    unsafe = {
        "changes a value": wording(PART_B.replace("0.28", "0.30")),
        "removes a value the key uses": wording(PART_B.replace(r"; use \(\ln(0.72)=-0.3285\)", "")),
        "rewrites the part": wording("Explain in your own words why logistic regression uses a log loss."),
        "names no such part": wording(PART_B.replace("screening", "triage"), "c"),
        "isn't a wording or rubric fix": wording(PART_B, "b", kind="fix_solution"),
        "gives away another question's reasoning": wording(PART_B.replace("Calculate", explanation + " Calculate")),
        "a part rubric with the wrong total": rubric_fix("a", 1, 1),
    }
    for reason, fix in unsafe.items():
        ai.on("review_exam", proposing(target["number"], fix))
        reviewed = review(api, exam["id"])
        assert snapshot(reviewed) == before, reason
        assert next(i for i in reviewed["review"]["issues"] if i["source"] == "ai")["fixApplied"] is False, reason
    assert revisions(api, target["id"]) == []


def test_the_reviewer_can_fix_one_parts_rubric_to_its_marks(api, supabase, db_a, ai):
    exam, target = exam_with_parts(api, supabase, db_a)
    half = target["points"] / 2
    old = copy.deepcopy(snapshot(exam)[target["id"]])
    ai.on("review_exam", proposing(target["number"], rubric_fix("a", half / 2, half / 2)))
    new = snapshot(review(api, exam["id"]))[target["id"]]
    assert new["subparts"][0]["rubric"] == [
        {"criterion": "Step 1", "points": half / 2},
        {"criterion": "Step 2", "points": half / 2},
    ]
    assert new["subparts"][1] == old["subparts"][1] and new["prompt"] == old["prompt"]
    # Without a part, a question with parts keeps its own (null) rubric: the parts carry the marks.
    ai.on("review_exam", proposing(target["number"], rubric_fix(None, target["points"])))  # type: ignore[arg-type]
    assert snapshot(review(api, exam["id"]))[target["id"]]["rubric"] is None


# -- Assessment targets: distinct cases, progressive concepts allowed -----------------------------


def plan_of(*targets: str, levels=("apply", "apply")) -> ExamPlan:
    questions = [
        slot(n, level).model_copy(update={"assessment_target": t})
        for n, (t, level) in enumerate(zip(targets, levels, strict=True), 1)
    ]
    return ExamPlan(
        sections=[PlannedSection(title="Q", instructions=None)], questions=questions, rationale="", coverage_notes="", warnings=[]
    )


def test_a_plan_with_the_same_task_twice_is_sent_back_and_progressive_targets_are_kept():
    spec = spec_with(None)
    _, soft = plan_problems(
        plan_of(
            "Compute the cross-entropy loss for y = 0 with f = 0.64", "Compute the cross-entropy loss for y = 0 with f = 0.88"
        ),
        spec,
        set(),
    )
    assert any("Questions 1 and 2 have nearly the same assessment target" in problem for problem in soft)
    # Similar wording is not the same task: the mean and the median are different skills.
    for different in (
        ("Compute the mean of a right-skewed sample", "Compute the median of a right-skewed sample"),
        ("Calculate the molar mass of sodium chloride", "Explain why sodium chloride dissolves in water"),
    ):
        assert not any("assessment target" in problem for problem in plan_problems(plan_of(*different), spec, set())[1])
    progressive = plan_of(
        "Recall the definition of the cross-entropy loss",
        "Analyse why a confident wrong prediction is penalised more than a hesitant one",
        levels=("remember", "analyze"),
    )
    assert not any("assessment target" in problem for problem in plan_problems(progressive, spec, set())[1])
    # Plans written before targets existed have none, and nothing is reported.
    assert not any("assessment target" in problem for problem in plan_problems(plan_of("", ""), spec, set())[1])


def test_every_writer_sees_every_questions_target(api, supabase, db_a, ai):
    from tests.model import well_behaved_plan

    tasks = ["recall", "define", "compute", "compare", "derive", "predict", "classify", "estimate", "critique", "design"]

    def with_targets(call):
        plan = well_behaved_plan(call)
        for question in plan.questions:
            question.assessment_target = f"{tasks[question.number - 1]} the idea of topic {question.number}"
        return plan

    ai.on("plan_exam", with_targets)
    assessment = run(seed_assessment(db_a, **{**A_SETTINGS, "number_of_versions": 1}))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    generate(api, assessment["id"])
    for call in ai.calls_for("generate_questions"):
        plan_text = call["input"].split("FULL EXAM PLAN")[1].split("QUESTIONS ALREADY WRITTEN")[0]
        assert all(f"target: {tasks[n - 1]} the idea of topic {n}" in plan_text for n in range(1, 11))
    assert not ai.calls_for("replan_exam")  # distinct targets: nothing to send back


# -- Formula warnings: only the professor's own formulas settle them ------------------------------

PROFESSOR = professor_formula_text(
    [
        "Use only these formulas:\n- Sigmoid: g(z) = 1/(1 + e^(−z)).\n- Loss: −ln(1 − f) for y = 0.\n"
        "- Displacement: s = ut + (1/2)at^2.",
        "Do not use the inconsistent cost J = 1/(2m) Σ L.",
    ]
)


def test_questions_whose_formulas_the_professor_wrote_are_traced_to_them():
    assert traced_to_professor(SIGMOID, PROFESSOR)  # the key; its distractors (wrong on purpose) don't count
    assert traced_to_professor(written(r"Explain \(L=-\ln(1-f)\) for a wrong, confident prediction."), PROFESSOR)
    assert traced_to_professor(written(r"A car starts with \(u=3\) m/s. Use \(s=ut+\frac{1}{2}at^{2}\)."), PROFESSOR)


def test_anything_else_keeps_its_warning():
    # A formula the professor forbade, or didn't write.
    assert not traced_to_professor(written(r"Compute \(J=\frac{1}{2m}\sum L\)."), PROFESSOR)
    assert not traced_to_professor(written(r"Use \(v=u+at\) to find v."), PROFESSOR)
    # A calculation with numbers can't be matched to a formula; nor can a question that states none.
    assert not traced_to_professor(written(r"Compute \(-\ln(1-0.28)=0.329\)."), PROFESSOR)
    assert not traced_to_professor(written(r"Let \(f=0.28\) and \(g(z)\) be the output."), PROFESSOR)
    # A formula only in the answer key still has to be the professor's.
    key_only = written("Explain saturation.")
    key_only.solution = r"Since \(g(z)=\frac{1}{1+e^{z}}\) tends to 0 …"
    assert not traced_to_professor(key_only, PROFESSOR)


def flagged_after_review(api, supabase, db_a, ai, professor_prompt: str) -> list[int]:
    def states_the_sigmoid(call):
        result = well_behaved_batch(call)
        if result.questions[0].number == 1:
            result.questions[0].prompt += r" Use \(\sigma(z)=\frac{1}{1+e^{-z}}\) to explain saturation."
        return result

    ai.on("analyze_lecture_segment", with_garbled_formulas)
    ai.on("plan_exam", plan_on_first_topic)
    ai.on("generate_questions", states_the_sigmoid)
    settings = {**A_SETTINGS, "number_of_versions": 1, "professor_prompt": professor_prompt}
    assessment = run(seed_assessment(db_a, **settings))
    run(seed_document(supabase, db_a, assessment["id"]))
    interpret_and_approve(api, assessment["id"])
    exam = generate(api, assessment["id"])
    return [i["questionNumber"] for i in exam["review"]["issues"] if "weren't reliably extracted" in i["message"]]


def test_a_formula_the_professor_wrote_needs_no_warning(api, supabase, db_a, ai):
    assert 1 not in flagged_after_review(api, supabase, db_a, ai, "Use the sigmoid σ(z) = 1/(1 + e^(−z)).")


def test_a_formula_the_professor_forbade_is_still_flagged(api, supabase, db_a, ai):
    assert 1 in flagged_after_review(api, supabase, db_a, ai, "Do not use σ(z) = 1/(1 + e^(−z)) on this exam.")


# -- Guidance reaches every step ------------------------------------------------------------------


def test_planner_writer_and_reviewer_guidance_for_partial_clues_targets_and_part_fixes():
    planner, writer, reviewer = (f(DataFramer()) for f in (planner_instructions, generator_instructions, reviewer_instructions))
    assert "assessment_target" in planner and "A concept may return only progressively" in planner
    assert "no question may supply what another asks students to produce" in planner
    assert "Partial clues count too" in writer and "a molar mass another question asks students to compute" in writer
    assert "write yours to its target" in writer
    assert "partial clues that materially reduce another question's work" in reviewer
    assert "OVERLAP" in reviewer and "set subpart_label to its label" in reviewer
    assert "keep every formula, number and table as it is" in reviewer
    assert SubpartOut.model_fields["rubric"]  # the parts' rubrics the reviewer may fix exist on every part
