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

  await expect.element(screen.getByRole("heading", { name: "Plan for question 2" })).toBeVisible();
  await expect.element(screen.getByText("Do task 2")).toBeVisible();
  await expect.element(screen.getByText("target 30")).toBeVisible();
  await expect.element(screen.getByText(/Lecture 4 is thin/)).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Write the whole exam" })).toBeVisible();
});

test("one question is written from its plan", async () => {
  serve(makePlan([planned(1), planned(2)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Write this question" }).nth(1).click();
  await expect
    .poll(() => posts().map(([path, options]) => [path, options?.body]))
    .toContainEqual(["/api/assessments/assessment-1/question-plan/2/question", { replace: false }]);
  await expect.element(screen.getByRole("button", { name: "Writing…" })).toBeVisible();
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

  await screen.getByRole("button", { name: "Write this question" }).click();
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
  await expect.element(screen.getByRole("button", { name: "Rewrite this question" })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Write the other 1 and finish" })).toBeVisible();
  await expect.element(screen.getByText(/rewrites question 1, whose plan you changed/)).toBeVisible();
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
  await screen.getByLabelText("Topic").fill("Load balancing");
  await screen.getByRole("button", { name: "Save" }).click();
  await expect.element(screen.getByText("Load balancing")).toBeVisible();
  const [, options] = vi.mocked(apiRequest).mock.calls.find(([, request]) => request?.method === "PATCH")!;
  expect(options?.body).toMatchObject({ topic: "Load balancing", points: 10, type: "mcq" });
});

test("writing the whole exam opens the exam page, where the questions appear", async () => {
  const { router } = await import("./mocks/navigation");
  serve(makePlan([planned(1, true), planned(2)]));
  const screen = await render(<QuestionPlanBuilder assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Write the other 1 and finish" }).click();
  await expect.poll(() => vi.mocked(router.push).mock.calls).toContainEqual(["/workspace/assessments/assessment-1/exam"]);
  expect(posts().map(([path]) => path)).toContain("/api/assessments/assessment-1/question-plan/exam");
});
