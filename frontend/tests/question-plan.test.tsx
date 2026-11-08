import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { QuestionPlanBuilder } from "@/components/plan/question-plan";
import { ApiError, apiRequest } from "@/lib/api/client";
import type { PlannedQuestion, QuestionPlan } from "@/lib/api/types";

import { makeQuestion } from "./fixtures";

function planned(number: number, written = false, outdated = false): PlannedQuestion {
  return {
    number,
    type: number === 1 ? "mcq" : "short_answer",
    difficulty: "medium",
    cognitiveLevel: "apply",
    points: 10,
    estimatedMinutes: 5,
    topic: `Topic ${number}`,
    learningObjective: `Objective ${number}`,
    assessmentTarget: `Do task ${number}`,
    sourceHint: "Lecture 1, slides 2–4",
    sourcePages: [],
    professorQuestion: "",
    outdated,
    written: written ? { ...makeQuestion(number - 1), number, position: number } : null,
  };
}

function makePlan(questions: PlannedQuestion[]): QuestionPlan {
  return {
    status: "ready",
    questions,
    rationale: "Covers every lecture.",
    warnings: ["Lecture 4 is thin. Fix: upload its notes."],
    summary: {
      questionCount: questions.length,
      totalPoints: questions.length * 10,
      estimatedMinutes: questions.length * 5,
      mcqPercent: 30,
      subjectivePercent: 70,
      easyPercent: 30,
      mediumPercent: 40,
      hardPercent: 30,
      bloom: null,
    },
    distributionWarnings: [],
    exam: questions.some((question) => question.written) ? { id: "exam-1", status: "building", errorMessage: null } : null,
  };
}

/** Serves the page's reads; `onPost` answers its writes. */
function serve(plan: QuestionPlan, onPost: (path: string, body: unknown) => unknown = () => ({ run: { id: "run-1" } })) {
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method === "POST" || options?.method === "PATCH") return onPost(url, options.body);
    if (url.endsWith("/question-plan")) return plan;
    if (url.endsWith("/ai-status")) return { runs: [], spec: { status: "approved" } };
    if (url.endsWith("/interpretation")) return { spec: { total_points: 30, duration_minutes: 20 } };
    if (url.startsWith("/api/runs/")) return { id: "run-1", kind: "generation", status: "running", stage: "generating_questions" };
    return null;
  });
}

const posts = () => vi.mocked(apiRequest).mock.calls.filter(([, options]) => options?.method === "POST");

test("each planned question can be read, and the targets and warnings are shown", async () => {
  serve(makePlan([planned(1), planned(2), planned(3)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  // Every question shows its planned topic.
  await expect.element(screen.getByRole("heading", { name: "Question 2: Topic 2" })).toBeVisible();
  await expect.element(screen.getByText("Do task 2")).toBeVisible();
  await expect.element(screen.getByText("target 30")).toBeVisible();
  await expect.element(screen.getByText(/Lecture 4 is thin/)).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Generate full exam" })).toBeVisible();
});

test("one question is generated from its plan", async () => {
  serve(makePlan([planned(1), planned(2)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Generate question" }).nth(1).click();
  await expect
    .poll(() => posts().map(([path, options]) => [path, options?.body]))
    .toContainEqual(["/api/assessments/assessment-1/question-plan/2/question", { replace: false }]);
  await expect.element(screen.getByRole("button", { name: "Generating…" })).toBeVisible();
});

test("an exam made another way is replaced only after the professor confirms", async () => {
  let refused = false;
  serve(makePlan([planned(1)]), (path, body) => {
    if (path.endsWith("/question") && !(body as { replace: boolean }).replace && !refused) {
      refused = true;
      throw new ApiError("exam_exists", "This assessment already has an exam.", 409);
    }
    return { run: { id: "run-1" } };
  });
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Generate question" }).click();
  await screen.getByRole("button", { name: "Replace exam" }).click();
  await expect
    .poll(() => posts().map(([, options]) => options?.body))
    .toContainEqual({ replace: true });
});

test("a written question shows under its plan with its answer key, and a changed plan is marked", async () => {
  serve(makePlan([planned(1, true, true), planned(2)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await expect.element(screen.getByText(/\(Question 1\)/)).toBeVisible();
  await expect.element(screen.getByText("Plan changed since it was written")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Regenerate question" })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Generate full exam" })).toBeVisible();
  await expect.element(screen.getByText(/regenerates question 1, whose plan you changed/)).toBeVisible();
});

test("the professor asks the AI to change the plan", async () => {
  serve(makePlan([planned(1), planned(2)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByLabelText("Tell ProfPilot what to change in the plan").fill("Make question 2 harder");
  await screen.getByRole("button", { name: "Change the plan" }).click();
  await expect
    .poll(() => posts().map(([path, options]) => [path, options?.body]))
    .toContainEqual(["/api/assessments/assessment-1/question-plan/changes", { instruction: "Make question 2 harder" }]);
});

test("the plan is edited by hand", async () => {
  serve(makePlan([planned(1)]), () => makePlan([{ ...planned(1), topic: "Load balancing" }]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Edit plan" }).click();
  await screen.getByLabelText("Topic", { exact: true }).fill("Load balancing");
  await screen.getByRole("button", { name: "Save" }).click();
  await expect.element(screen.getByText("Load balancing")).toBeVisible();
  const [, options] = vi.mocked(apiRequest).mock.calls.find(([, request]) => request?.method === "PATCH")!;
  expect(options?.body).toMatchObject({ topic: "Load balancing", points: 10, type: "mcq" });
});

test("generating the full exam keeps the questions already generated and opens the exam page", async () => {
  const { router } = await import("./mocks/navigation");
  serve(makePlan([planned(1, true), planned(2), planned(3)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await expect.element(screen.getByText(/generate the 2 remaining questions, keeping the 1 you already generated/)).toBeVisible();
  await screen.getByRole("button", { name: "Generate full exam" }).click();
  await expect.poll(() => vi.mocked(router.push).mock.calls).toContainEqual(["/workspace/assessments/assessment-1/exam"]);
  expect(posts().map(([path]) => path)).toContain("/api/assessments/assessment-1/question-plan/exam");
});

test("a plan still being prepared shows its progress and isn't started twice", async () => {
  serve({ ...makePlan([]), status: "none" });
  const serveNext = vi.mocked(apiRequest).getMockImplementation()!;
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (url.endsWith("/ai-status")) {
      return { runs: [{ id: "run-9", kind: "interpretation", status: "running", stage: "planning" }], spec: { status: "approved" } };
    }
    if (url.startsWith("/api/runs/")) return { id: "run-9", kind: "interpretation", status: "running", stage: "planning" };
    return serveNext(path, options);
  });
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await expect.element(screen.getByText(/Planning the questions: 0 planned so far/)).toBeVisible();
  await expect.element(screen.getByText("Planning question 1…")).toBeVisible();
  expect(posts()).toEqual([]);
});

test("building the exam in a conversation is offered after the plan", async () => {
  const { router } = await import("./mocks/navigation");
  serve(makePlan([planned(1)]), () => ({ exam: { id: "exam-1" } }));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Build with AI" }).click();
  await expect.poll(() => vi.mocked(router.push).mock.calls).toContainEqual(["/workspace/assessments/assessment-1/builder"]);
  expect(posts().map(([path, options]) => [path, options?.body])).toContainEqual([
    "/api/assessments/assessment-1/exam",
    { mode: "interactive", replace: false },
  ]);
});

test("a plan still being made: its case comes first, and a planned question is changed and generated while the rest loads", async () => {
  const caseText = "A clinic screens 1,000 patients. The test flags 120, of whom 90 are ill.";
  let plan: QuestionPlan = {
    ...makePlan([
      { ...planned(1), sectionIndex: 0 },
      { ...planned(2), sectionIndex: 0 },
    ]),
    status: "planning",
    expectedQuestions: 5,
    sections: [{ title: "Case Study 1: Screening", instructions: caseText }],
  };
  serve(plan, (path) => (path.endsWith("/question-plan/2") ? { ...plan, questions: [plan.questions[0], { ...plan.questions[1], topic: "Recall" }] } : { run: { id: "run-2" } }));
  const serveNext = vi.mocked(apiRequest).getMockImplementation()!;
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method === undefined && url.endsWith("/question-plan")) return plan;
    if (url.endsWith("/ai-status")) {
      return { runs: [{ id: "run-9", kind: "interpretation", status: "running", stage: "planning" }], spec: { status: "approved" } };
    }
    if (url === "/api/runs/run-9") return { id: "run-9", kind: "interpretation", status: "running", stage: "planning" };
    return serveNext(path, options);
  });
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  // The case is shown before the questions built on it, and the rest of the plan is still loading.
  await expect.element(screen.getByRole("heading", { name: "Case Study 1: Screening" })).toBeVisible();
  await expect.element(screen.getByText(caseText)).toBeVisible();
  await expect.element(screen.getByRole("heading", { name: "Question 2: Topic 2" })).toBeVisible();
  await expect.element(screen.getByText(/Planning question 3… About 3 more to plan\./)).toBeVisible();
  await expect.element(screen.getByText(/Planning the questions: 2 of about 5 planned so far/)).toBeVisible();
  // The full exam waits for the whole plan; one planned question doesn't.
  await expect.element(screen.getByRole("button", { name: "Generate full exam" })).toBeDisabled();

  // A planned question is changed while the rest is planned…
  await screen.getByRole("button", { name: "Edit plan" }).nth(1).click();
  await screen.getByLabelText("Topic", { exact: true }).fill("Recall");
  await screen.getByRole("button", { name: "Save" }).click();
  await expect.element(screen.getByRole("heading", { name: "Question 2: Recall" })).toBeVisible();

  // …and generated.
  await screen.getByRole("button", { name: "Generate question" }).nth(1).click();
  await expect
    .poll(() => posts().map(([path, options]) => [path, options?.body]))
    .toContainEqual(["/api/assessments/assessment-1/question-plan/2/question", { replace: false }]);

  // The next planned question appears at the next refresh, under its case.
  plan = { ...plan, questions: [...plan.questions, { ...planned(3), sectionIndex: 0 }] };
  await expect.element(screen.getByRole("heading", { name: "Question 3: Topic 3" }), { timeout: 5000 }).toBeVisible();
});

test("a plan finished while the professor was away is shown whole at once", async () => {
  serve({
    ...makePlan([
      { ...planned(1), sectionIndex: 0, task: "conceptual" },
      { ...planned(2), sectionIndex: 0, task: "calculation" },
    ]),
    sections: [{ title: "Case Study 1: Screening", instructions: "A clinic screens 1,000 patients." }],
    tasks: { conceptual: 1, application: 0, calculation: 1 },
    previousTasks: { conceptual: 18, application: 11, calculation: 1 },
  });
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await expect.element(screen.getByRole("heading", { name: "Question 2: Topic 2" })).toBeVisible();
  expect(screen.getByText(/Planning question/).query()).toBeNull();
  await expect.element(screen.getByRole("button", { name: "Generate full exam" })).toBeEnabled();
  // What students do, next to the professor's previous exams.
  await expect.element(screen.getByText(/1 conceptual · 0 application · 1 calculation/)).toBeVisible();
  await expect.element(screen.getByText(/your previous exams: 18 conceptual · 11 application · 1 calculation \(of 30\)/)).toBeVisible();
});
