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

  await expect.element(screen.getByText("Generating the exam: 2 of 3 questions ready")).toBeVisible();
  // Numbered by the plan, not by arrival: question 3 is "Question 3" even before question 2 exists.
  await expect.element(screen.getByRole("heading", { name: "Question 3" })).toBeVisible();
  await expect.element(screen.getByText("Generating question 2…")).toBeVisible();
  // Reading only: nothing can be changed until the whole exam is written.
  expect(screen.getByRole("button", { name: "Approve" }).query()).toBeNull();

  // Question 2 arrives at the next refresh.
  exam = generating([1, 2, 3]);
  await expect.element(screen.getByRole("heading", { name: "Question 2" }), { timeout: 5000 }).toBeVisible();
  await expect.element(screen.getByText("Generating the exam: 3 of 3 questions ready")).toBeVisible();
});

test("while the exam is written, each case is shown with its questions, before they are ready", async () => {
  const caseText = "A clinic screens 1,000 patients. The test flags 120, of whom 90 are ill.";
  const exam: Exam = {
    ...generating([1]),
    plannedSections: [0, 0, 1],
    sections: [
      { id: "section-1", position: 1, title: "Case Study 1: Screening", instructions: caseText },
      { id: "section-2", position: 2, title: "Short questions", instructions: null },
    ],
  };
  exam.questions = exam.questions.map((question) => ({ ...question, sectionId: "section-1" }));
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    const url = String(path);
    if (url.endsWith("/exam")) return exam;
    if (url.endsWith("/ai-status")) return { runs: [{ id: "run-1", kind: "generation", status: "running" }] };
    if (url.startsWith("/api/runs/")) return { id: "run-1", kind: "generation", status: "running", stage: "generating_questions" };
    return null;
  });
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  const caseSection = screen.getByRole("region", { name: "Case Study 1: Screening" });
  await expect.element(caseSection.getByText(caseText)).toBeVisible();
  await expect.element(caseSection.getByRole("heading", { name: "Question 1" })).toBeVisible();
  // Question 2 isn't written yet, and is already under its case.
  await expect.element(caseSection.getByText("Generating question 2…")).toBeVisible();
  const second = screen.getByRole("region", { name: "Short questions" });
  await expect.element(second.getByText("Generating question 3…")).toBeVisible();
});
