import { beforeEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { PlanReview } from "@/components/plan/plan-review";
import { apiRequest } from "@/lib/api/client";
import type { ExamSpec, Interpretation } from "@/lib/api/types";

import { router } from "./mocks/navigation";

const spec = {
  assessment_title: null, course: null, assessment_type: null, duration_minutes: 60, duration_source: "professor",
  versions: 1, total_points: 100, total_points_source: "professor", question_count_min: 3, question_count_max: 3,
  question_count_source: "professor", question_format: null, difficulty: null, bloom: null, sections: [], coverage: [],
  learning_emphasis: [], question_style: [], professor_notes: [], professor_preferences_applied: [],
  source_material_guidance: null, previous_exam_style_guidance: null, constraints: [], assumptions: [], warnings: [],
  generation_instructions: [],
} as unknown as ExamSpec;

const interpretation = {
  status: "draft", staleReasons: [], spec, enhancedPrompt: "Write a 60-minute midterm on lectures 3 to 5.", inputs: {},
} as unknown as Interpretation;

/** The prompt page's requests. `plan` is what the plan endpoint has; `runs` the jobs already running. */
function serve({ plan = "none", runs = [] as object[], planRun = "running" } = {}) {
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (url.endsWith("/interpretation")) return interpretation;
    if (url.endsWith("/ai-status")) return { runs, exam: null, spec: { status: "draft" } };
    if (url.endsWith("/question-plan") && options?.method === "POST") return { run: { id: "plan-run" } };
    if (url.endsWith("/question-plan")) return { status: plan, questions: [] };
    if (url.endsWith("/interpretation/approval")) return { ...interpretation, status: "approved" };
    if (url.startsWith("/api/runs/")) return { id: "plan-run", kind: "interpretation", status: planRun, stage: "planning" };
    return null;
  });
}

const posts = () => vi.mocked(apiRequest).mock.calls.filter(([, options]) => options?.method === "POST").map(([path]) => path);

beforeEach(() => {
  router.push.mockClear();
});

test("the prompt page only moves on to the plan: nothing is generated from it", async () => {
  serve();
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByRole("button", { name: "Next: go to plan" })).toBeVisible();
  for (const name of ["Build with AI", "Generate full exam", "Generate exam"]) {
    expect(screen.getByRole("button", { name }).query(), name).toBeNull();
  }
});

test("the plan is prepared in the background while the prompt is read", async () => {
  serve({ planRun: "running" });
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByText(/Preparing your question plan in the background/)).toBeVisible();
  expect(posts()).toEqual(["/api/assessments/a1/question-plan"]);
});

test("the page says when the plan is ready", async () => {
  serve({ planRun: "succeeded" });
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByText("Your question plan is ready.")).toBeVisible();
});

test("a plan already being prepared is followed, not started again", async () => {
  serve({ runs: [{ id: "plan-run", kind: "interpretation", status: "running", stage: "planning" }] });
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByText(/Preparing your question plan in the background/)).toBeVisible();
  await expect.element(screen.getByText("Write a 60-minute midterm on lectures 3 to 5.")).toBeVisible();
  expect(posts()).toEqual([]);
});

test("a ready plan isn't made again, and Next opens it", async () => {
  serve({ plan: "ready" });
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByText("Your question plan is ready.")).toBeVisible();
  await screen.getByRole("button", { name: "Next: go to plan" }).click();
  await vi.waitFor(() => expect(router.push).toHaveBeenCalledWith("/workspace/assessments/a1/question-plan"));
  expect(posts()).toEqual(["/api/assessments/a1/interpretation/approval"]);
});
