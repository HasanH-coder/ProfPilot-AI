import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { ExamWorkspace } from "@/components/exam/exam-workspace";
import { MathText } from "@/components/exam/question-text";
import { PlanReview } from "@/components/plan/plan-review";
import { apiRequest } from "@/lib/api/client";
import type { AiStatus, Exam, ExamSpec, Interpretation, Question } from "@/lib/api/types";

import { makeExam, makeQuestion } from "./fixtures";

// A finding like the one the v3 quality check reported, with maths in it.
const LEAK = String.raw`Q4 gives \(e^{-0.8}\) and calls \(f\) the predicted class-\(1\) probability, cueing Q1 and Q3.`;

test("prose renders only explicit \\( \\) and \\[ \\] maths and keeps everything else as text", async () => {
  const screen = await render(
    <p data-testid="prose">
      <MathText text={String.raw`Use \(\frac{1}{1+e^{-z}}\) and \[z = w^\top x\]. It costs $5 and $10; see p2 and x1. <b>bold</b> \(\unknownmacro{x}\)`} />
    </p>,
  );
  const prose = screen.getByTestId("prose").element();
  expect(prose.querySelectorAll(".katex")).toHaveLength(2);
  expect(prose.querySelector("math mfrac")).not.toBeNull();
  // Dollar amounts and names like p2 or x1 aren't maths here.
  expect(prose.textContent).toContain("It costs $5 and $10; see p2 and x1.");
  // Markup in the text is shown as text, never run.
  expect(prose.querySelector("b")).toBeNull();
  expect(prose.textContent).toContain("<b>bold</b>");
  // Notation KaTeX can't render stays visible as written.
  expect(prose.textContent).toContain(String.raw`\(\unknownmacro{x}\)`);
  // Without maths, the text is exactly as it was.
  const plain = await render(<span data-testid="plain"><MathText text="Two questions test the same case." /></span>);
  expect(plain.getByTestId("plain").element().innerHTML).toBe("Two questions test the same case.");
});

function serveExam(exam: Exam) {
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    const url = String(path);
    if (url.endsWith("/exam")) return exam;
    if (url.endsWith("/ai-status")) return { runs: [] };
    return null;
  });
}

test("the quality check shows maths in its findings as formulas", async () => {
  const exam = makeExam(2);
  serveExam({
    ...exam,
    status: "ready",
    reviewStatus: "needs_attention",
    review: {
      summary: String.raw`Mostly sound; one cue about \(e^{-z}\).`,
      checkedAt: "2026-10-19T09:29:08Z",
      fixesApplied: 0,
      estimatedTotalMinutes: 30,
      issues: [
        { id: "i5", severity: "warning", category: "answer_leakage", message: LEAK, questionId: "question-2", questionNumber: 2, versionLabel: null, source: "ai", fixApplied: false },
      ],
    },
  });
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);
  const finding = screen.getByText(/calls/).first();
  await expect.element(finding).toBeVisible();
  const item = finding.element().closest("li")!;
  expect(item.querySelectorAll(".katex")).toHaveLength(3);
  expect(item.textContent).not.toContain(String.raw`\(`);
  expect(screen.getByText(/Mostly sound/).element().querySelector(".katex")).not.toBeNull();
});

test("each part's marking rubric is shown with its stored marks, and a mismatch is pointed out", async () => {
  const problem: Question = {
    ...makeQuestion(2),
    id: "question-3",
    points: 6,
    prompt: "Score one container.",
    answer: null,
    // An overall rubric beside the parts' rubrics: its marks aren't checked against the question's (as in the backend).
    rubric: [{ criterion: "Clear working throughout", points: 1 }],
    subparts: [
      {
        label: "a", prompt: String.raw`Compute \(z\).`, points: 2, answer: String.raw`\(z = -0.700\)`,
        rubric: [{ criterion: String.raw`Correct substitution into \(z = w^\top x + b\)`, points: 1 }, { criterion: "Correct value", points: 1 }],
      },
      {
        label: "b", prompt: String.raw`Compute \(g(0.8)\).`, points: 2, answer: "0.690",
        rubric: [{ criterion: "Uses the sigmoid", points: 1 }, { criterion: "Correct value", points: 1.5 }],
      },
      // Saved before parts had rubrics: nothing to show, and nothing breaks.
      { label: "c", prompt: "Interpret.", points: 2, answer: "Low loss.", rubric: undefined as unknown as [] },
    ],
  };
  const exam = makeExam(2); // Question 2 has a question-level rubric.
  serveExam({ ...exam, status: "ready", questions: [...exam.questions, problem] });
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  const partA = screen.getByRole("table", { name: "Marking rubric for part (a)" });
  await expect.element(partA).toBeVisible();
  const rowsA = [...partA.element().querySelectorAll("tbody tr")].map((row) => row.lastElementChild!.textContent);
  expect(rowsA).toEqual(["1", "1"]);
  expect(partA.element().querySelector("tbody td .katex")).not.toBeNull();
  // (a) adds up; (b) doesn't, and says so.
  const partB = screen.getByRole("table", { name: "Marking rubric for part (b)" });
  expect(partA.element().parentElement!.textContent).not.toContain("adds up to");
  expect(partB.element().parentElement!.textContent).toContain("This rubric adds up to 2.5 marks, but it is worth 2 marks.");
  expect(screen.getByRole("table", { name: "Marking rubric for part (c)" }).query()).toBeNull();

  // Question-level rubrics are still shown: Question 2's, and the multi-part question's without a false warning.
  const overall = screen.getByRole("table", { name: "Marking rubric", exact: true });
  await expect.element(overall.first()).toBeVisible();
  const multiPart = overall.all().find((table) => table.element().textContent?.includes("Clear working"))!;
  expect(multiPart.element().parentElement!.textContent).not.toContain("adds up to");
  // Hiding answer keys hides the part rubrics too.
  await screen.getByRole("checkbox", { name: "Show answer keys" }).click();
  expect(screen.getByRole("table", { name: "Marking rubric for part (a)" }).query()).toBeNull();
});

test("the plan shows maths in the brief, warnings, assumptions, notes and plan items", async () => {
  const spec = {
    assessment_title: null, course: null, assessment_type: null, duration_minutes: 30, duration_source: "professor",
    versions: 1, total_points: 20, total_points_source: "professor", question_count_min: 5, question_count_max: 5,
    question_count_source: "professor", question_format: null, difficulty: null, bloom: null,
    sections: [], coverage: [{ topic: String.raw`Sigmoid \(g(z)\)`, emphasis: "high", source_documents: [], source: "professor" }],
    learning_emphasis: [], question_style: [],
    professor_notes: [{ text: String.raw`Use \(J(\theta)\) as on the slides.`, source: "professor" }],
    professor_preferences_applied: [], source_material_guidance: null, previous_exam_style_guidance: null, constraints: [],
    assumptions: [String.raw`Students know \(\ln\).`],
    warnings: [String.raw`The slides write the cost as \(-\log(1-h)\) in one place.`],
    generation_instructions: [],
  } as unknown as ExamSpec;
  const interpretation = {
    status: "draft", staleReasons: [], spec,
    enhancedPrompt: String.raw`Objectives: test \(g(z) = \frac{1}{1+e^{-z}}\).
Coverage: cost at $0 budget.
Constraints: no calculators.`,
    inputs: { professor_prompt: "Five questions.", additional_notes: String.raw`\(h_\theta(x) = g(\theta^\top x)\)` },
  } as unknown as Interpretation;
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    const url = String(path);
    if (url.endsWith("/interpretation")) return interpretation;
    if (url.endsWith("/ai-status")) return { runs: [], exam: null } as unknown as AiStatus;
    return null;
  });
  const screen = await render(<PlanReview assessmentId="assessment-1" />);

  for (const text of [/test/, /one place/, /Students know/, /as on the slides/, /Sigmoid/]) {
    const element = screen.getByText(text).first();
    await expect.element(element).toBeVisible();
    expect(element.element().querySelector(".katex"), String(text)).not.toBeNull();
    expect(element.element().textContent).not.toContain(String.raw`\(`);
  }
  await expect.element(screen.getByText("cost at $0 budget.")).toBeVisible();
  expect(screen.getByRole("definition").filter({ hasText: "h" }).first().element().querySelector(".katex")).not.toBeNull();
});
