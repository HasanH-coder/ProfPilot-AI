import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { ExamWorkspace } from "@/components/exam/exam-workspace";
import { apiRequest } from "@/lib/api/client";
import type { Exam } from "@/lib/api/types";

import { makeExam, makeQuestion } from "./fixtures";

/** An exam being written: questions 1 and 3 are done (each at its planned number), 2 is still being written. */
function generating(positions: number[]): Exam {
  return {
    ...makeExam(0),
    mode: "full",
    status: "generating",
    plannedQuestionCount: 3,
    questions: positions.map((position) => ({ ...makeQuestion(position - 1), id: `q${position}`, number: 0, position })),
  };
}

test("while the exam is written, each question appears in order as soon as it is ready", async () => {
  let exam = generating([1, 3]);
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    const url = String(path);
    if (url.endsWith("/exam")) return exam;
    if (url.endsWith("/ai-status")) return { runs: [{ id: "run-1", kind: "generation", status: "running" }] };
    if (url.startsWith("/api/runs/")) return { id: "run-1", kind: "generation", status: "running", stage: "generating_questions" };
    return null;
  });
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  await expect.element(screen.getByText("Writing the exam: 2 of 3 questions ready")).toBeVisible();
  // Numbered by the plan, not by arrival: question 3 is "Question 3" even before question 2 exists.
  await expect.element(screen.getByRole("heading", { name: "Question 3" })).toBeVisible();
  await expect.element(screen.getByText("Writing question 2 and its answer key…")).toBeVisible();
  // Reading only: nothing can be changed until the whole exam is written.
  expect(screen.getByRole("button", { name: "Approve" }).query()).toBeNull();

  // Question 2 arrives at the next refresh.
  exam = generating([1, 2, 3]);
  await expect.element(screen.getByRole("heading", { name: "Question 2" }), { timeout: 5000 }).toBeVisible();
  await expect.element(screen.getByText("Writing the exam: 3 of 3 questions ready")).toBeVisible();
});
