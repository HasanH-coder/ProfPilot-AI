import { beforeEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { PlanReview } from "@/components/plan/plan-review";
import { QuestionPlanReview } from "@/components/plan/question-plan-review";
import { apiRequest } from "@/lib/api/client";
import type { QuestionPlan } from "@/lib/api/types";
import { demoRedirect } from "@/lib/demo";
import type { CourseSource } from "@/lib/sources/types";

import { deferred, professor } from "./fixtures";
import { router } from "./mocks/navigation";

vi.mock("@/lib/demo", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/demo")>()),
  DEMO_MODE: true,
}));

const planned: QuestionPlan = {
  status: "ready",
  rationale: "Five questions across the lecture's main ideas.",
  questions: [1, 2].map((number) => ({
    number,
    type: "mcq",
    difficulty: "medium",
    cognitiveLevel: "apply",
    points: 2,
    estimatedMinutes: 2,
    topic: `Topic ${number}`,
    assessmentTarget: `Do task ${number}`,
    sourceHint: "Lecture 1, slides 2-4",
    professorQuestion: "",
  })),
};

const approvedStatus = { spec: { status: "approved" }, runs: [], exam: null };

/** Answers the plan page's requests, and records what it sends. */
function servePlan(plan: QuestionPlan, status: object = approvedStatus) {
  const sent: { url: string; method?: string; body?: unknown }[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method) sent.push({ url, method: options.method, body: options.body });
    if (url.endsWith("/ai-status")) return status;
    if (url.endsWith("/question-plan") && options?.method === "POST") return { run: { id: "run-1" } };
    if (url.endsWith("/question-plan")) return plan;
    if (url.includes("/question-plan/")) {
      return {
        ...plan,
        questions: plan.questions.map((question) =>
          url.endsWith(`/${question.number}`) ? { ...question, ...(options?.body as object) } : question,
        ),
      };
    }
    if (url.startsWith("/api/runs/")) return { id: "run-1", kind: "interpretation", status: "running", stage: "planning" };
    return { run: { id: "run-2" } };
  });
  return sent;
}

beforeEach(() => {
  router.push.mockClear();
});

test("the form shows only the demo's steps, with the mini quiz as the one duration", async () => {
  const screen = await render(<AssessmentForm courses={[]} />);

  await expect.element(screen.getByRole("heading", { name: "Course material" })).toBeVisible();
  await expect.element(screen.getByRole("heading", { name: "Previous assessments" })).toBeVisible();
  await expect.element(screen.getByText("Question distribution", { exact: true })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Improve with AI" })).toBeVisible();

  const quiz = screen.getByRole("button", { name: "10 min mini quiz" });
  await expect.element(quiz).toHaveAttribute("aria-pressed", "true");
  for (const name of ["Not specified", "60 min", "90 min", "120 min", "Custom", "Continue"]) {
    expect(screen.getByRole("button", { name, exact: true }).query()).toBeNull();
  }
  for (const text of ["Set up with AI", "Course & Assessment Details", "Additional notes", "Additional images or attachments"]) {
    expect(screen.getByRole("heading", { name: text }).query()).toBeNull();
  }
  await expect.element(screen.getByText("Difficulty distribution", { exact: true })).toBeVisible();
});

test("choosing a course lists the material saved in Sources, with no assessment name to fill in", async () => {
  const courses = [{ id: "course-1", code: "CMPS 261", name: "Machine Learning" }];
  const saved: CourseSource = {
    id: "lecture-1",
    courseId: "course-1",
    category: "course_material",
    name: "Lecture 1.pdf",
    size: 1000,
    status: "ready",
    error: null,
    pageCount: 3,
    title: null,
    createdAt: "2026-10-16T10:00:00Z",
    knowledge: { state: "none", topics: null },
  };

  const none = await render(<AssessmentForm courses={courses} sources={[saved]} />);
  await expect.element(none.getByRole("heading", { name: /^Course\b(?! material)/ })).toBeVisible();
  await expect.element(none.getByText("Choose a course to use material saved in Sources.")).toBeVisible();
  expect(none.getByLabelText("Assessment name").query()).toBeNull();
  await none.unmount();

  const draft = {
    courseId: "course-1",
    examName: "10-minute mini quiz",
    durationMinutes: 10,
    mcqPercentage: null,
    subjectivePercentage: null,
    numberOfVersions: 1,
    easyPercentage: null,
    mediumPercentage: null,
    hardPercentage: null,
    learningFocus: "automatic" as const,
    bloomDistribution: null,
    additionalNotes: "",
    professorPrompt: "",
  };
  const chosen = await render(
    <AssessmentForm
      courses={courses}
      sources={[saved]}
      saved={{ examProjectId: "draft-1", folder: "prof/draft-1", draft, files: [], sourceIds: [] }}
    />,
  );
  await expect.element(chosen.getByText("Or use material saved in Sources")).toBeVisible();
  await expect.element(chosen.getByText("Lecture 1.pdf")).toBeVisible();
  expect(chosen.getByText("Choose a course to use material saved in Sources.").query()).toBeNull();
});

test("the sidebar has only the demo's pages", async () => {
  const screen = await render(<WorkspaceSidebar professor={professor} />);
  const links = screen.getByRole("navigation", { name: "Workspace" }).getByRole("link");
  expect(links.elements().map((link) => link.textContent)).toEqual(["New assessment", "Sources", "Assessments"]);
});

test("the demo sends hidden pages away and keeps its own", () => {
  const home = "/workspace/assessments/new";
  expect(demoRedirect("/", false)).toBe("/login");
  expect(demoRedirect("/", true)).toBe(home);
  expect(demoRedirect("/workspace", true)).toBe(home);
  expect(demoRedirect("/workspace/courses", true)).toBe(home);
  expect(demoRedirect("/workspace/preferences", true)).toBe(home);
  expect(demoRedirect("/workspace/assessments/a1/builder", true)).toBe("/workspace/assessments/a1/exam");
  for (const page of [home, "/workspace/sources", "/workspace/assessments", "/workspace/assessments/a1/plan", "/workspace/assessments/a1/question-plan", "/login"]) {
    expect(demoRedirect(page, true)).toBeNull();
  }
});

test("the prompt confirmation page confirms and moves to the AI plan, with no call feature", async () => {
  const sent: string[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method) sent.push(url);
    if (url.endsWith("/ai-status")) return { spec: { status: "draft" }, runs: [], exam: null };
    if (url.endsWith("/question-plan")) return { status: "ready", questions: [], rationale: null };
    return {
      status: "draft",
      staleReasons: [],
      enhancedPrompt: "A complete brief.",
      spec: { assumptions: [], warnings: [], coverage: [], sections: [] },
      inputs: {},
    };
  });
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByRole("heading", { name: "Your improved prompt" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Build with AI" }).query()).toBeNull();
  expect(screen.getByRole("heading", { name: "Assessment plan" }).query()).toBeNull();

  await screen.getByRole("button", { name: "Next" }).click();
  await vi.waitFor(() => expect(router.push).toHaveBeenCalledWith("/workspace/assessments/a1/question-plan"));
  expect(sent).toEqual(["/api/assessments/a1/interpretation/approval"]);
});

test("the plan says what ProfPilot will ask, and generating writes it", async () => {
  const sent = servePlan(planned);
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);

  await expect.element(screen.getByText("Five questions across the lecture's main ideas.")).toBeVisible();
  await expect.element(screen.getByText(/I'm planning one question on/).first()).toBeVisible();
  await expect.element(screen.getByText("Topic 1")).toBeVisible();
  await expect.element(screen.getByText("Students will: Do task 2")).toBeVisible();

  await screen.getByRole("button", { name: "Generate exam" }).click();
  await vi.waitFor(() => expect(router.push).toHaveBeenCalledWith("/workspace/assessments/a1/exam"));
  expect(sent).toEqual([{ url: "/api/assessments/a1/exam", method: "POST", body: { mode: "full", replace: false } }]);
});

test("the professor can change a planned question", async () => {
  const sent = servePlan(planned);
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);
  const card = screen.getByRole("article", { name: "Question 2" });

  await card.getByRole("button", { name: "Edit" }).click();
  await card.getByRole("textbox", { name: "Topic" }).fill("Virtual memory");
  await card.getByRole("button", { name: "Save" }).click();

  await expect.element(card.getByText("Virtual memory")).toBeVisible();
  expect(sent).toEqual([
    {
      url: "/api/assessments/a1/question-plan/2",
      method: "PATCH",
      body: { topic: "Virtual memory", assessmentTarget: "Do task 2", professorQuestion: "" },
    },
  ]);
});

test("the professor can give their own question", async () => {
  const sent = servePlan(planned);
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);
  const card = screen.getByRole("article", { name: "Question 1" });

  await card.getByRole("button", { name: "Give your own question" }).click();
  await card.getByRole("textbox", { name: "Your own question" }).fill("What does a TLB cache?");
  await card.getByRole("button", { name: "Save" }).click();

  await expect.element(card.getByText("What does a TLB cache?")).toBeVisible();
  expect(sent[0].body).toEqual({ topic: "Topic 1", assessmentTarget: "Do task 1", professorQuestion: "What does a TLB cache?" });
});

test("with no plan yet, planning starts by itself and shows its progress", async () => {
  const sent = servePlan({ status: "none", questions: [], rationale: null });
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);

  await expect.element(screen.getByText("Planning the assessment…")).toBeVisible();
  expect(sent).toEqual([{ url: "/api/assessments/a1/question-plan", method: "POST", body: undefined }]);
});

test("when planning finishes, the plan replaces the progress without flashing 'Try again'", async () => {
  const sent = servePlan({ status: "none", questions: [], rationale: null });
  const serve = vi.mocked(apiRequest).getMockImplementation()!;
  const finishedPlan = deferred<QuestionPlan>();
  let planReads = 0;
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (url.startsWith("/api/runs/")) return { id: "run-1", kind: "interpretation", status: "succeeded", stage: "done" };
    // The first read is the page loading; the next one fetches the finished plan, which is slow here.
    if (url.endsWith("/question-plan") && !options?.method && ++planReads > 1) return finishedPlan.promise;
    return serve(path, options);
  });
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);

  await vi.waitFor(() => expect(planReads).toBeGreaterThan(1));
  expect(screen.getByText("ProfPilot hasn't planned the questions yet.").query()).toBeNull();
  expect(screen.getByRole("button", { name: "Try again" }).query()).toBeNull();
  finishedPlan.resolve(planned);
  await expect.element(screen.getByText("Topic 1")).toBeVisible();
  expect(sent.filter((request) => request.method === "POST")).toHaveLength(1);
});

test("a planning failure offers Try again and doesn't start again by itself", async () => {
  const none: QuestionPlan = { status: "none", questions: [], rationale: null };
  const sent = servePlan(none);
  const serve = vi.mocked(apiRequest).getMockImplementation()!;
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    if (String(path).startsWith("/api/runs/")) return { id: "run-1", status: "failed", errorMessage: "The AI service is busy." };
    return serve(path, options);
  });
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);

  await expect.element(screen.getByText("The AI service is busy.")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  expect(sent.filter((request) => request.method === "POST")).toHaveLength(1);
});

test("a plan needs a confirmed prompt first", async () => {
  const sent = servePlan({ status: "none", questions: [], rationale: null }, { ...approvedStatus, spec: { status: "draft" } });
  const screen = await render(<QuestionPlanReview assessmentId="a1" />);

  await expect.element(screen.getByRole("link", { name: "Confirm your prompt" })).toHaveAttribute("href", "/workspace/assessments/a1/plan");
  expect(sent).toEqual([]);
});

/** The prompt page's requests, with the plan either waiting to be made or already being made. */
function servePrompt(runs: object[], plan: QuestionPlan = { status: "none", questions: [], rationale: null }) {
  const sent: string[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method) sent.push(`${options.method} ${url}`);
    if (url.endsWith("/ai-status")) return { spec: { status: "draft" }, runs, exam: null };
    if (url.endsWith("/question-plan")) return options?.method ? { run: { id: "run-9" } } : plan;
    return {
      status: "draft",
      staleReasons: [],
      enhancedPrompt: "A short prompt, in one paragraph.",
      spec: { assumptions: ["A total of 10 marks."], warnings: [], coverage: [], sections: [] },
      inputs: {},
    };
  });
  return sent;
}

test("the plan starts being made as soon as the prompt is on screen", async () => {
  const sent = servePrompt([]);
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByRole("button", { name: "Next" })).toBeVisible();
  await vi.waitFor(() => expect(sent).toEqual(["POST /api/assessments/a1/question-plan"]));
});

test("a plan that already exists isn't made again", async () => {
  const sent = servePrompt([], planned);
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByRole("button", { name: "Next" })).toBeVisible();
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(sent).toEqual([]);
});

test("the prompt stays readable while the plan is being made", async () => {
  const sent = servePrompt([{ id: "run-9", kind: "interpretation", status: "running", stage: "planning" }]);
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByText("A short prompt, in one paragraph.")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  expect(sent).toEqual([]);
});

test("the prompt page is simple: no assumptions list", async () => {
  servePrompt([], planned);
  const screen = await render(<PlanReview assessmentId="a1" />);

  await expect.element(screen.getByRole("button", { name: "Edit prompt" })).toBeVisible();
  expect(screen.getByText("A total of 10 marks.").query()).toBeNull();
});
