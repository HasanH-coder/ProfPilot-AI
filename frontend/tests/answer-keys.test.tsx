import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { ExamWorkspace } from "@/components/exam/exam-workspace";
import { QuestionCard } from "@/components/exam/question-card";
import { ApiError, apiDownload, apiRequest } from "@/lib/api/client";
import type { Exam, Question } from "@/lib/api/types";

import { makeExam, makeQuestion } from "./fixtures";

/** A question as the plan writes it: the question and its choices, no answer key yet. */
function keyless(index: number): Question {
  return {
    ...makeQuestion(index),
    correctChoice: null,
    answer: null,
    solution: null,
    rubric: null,
    explanation: null,
    hasAnswerKey: false,
    answerKeyOutdated: false,
  };
}

const writes = () => vi.mocked(apiRequest).mock.calls.filter(([, options]) => options?.method && options.method !== "GET");

test("a question without a key offers its key, and is edited by typing without choosing an answer", async () => {
  vi.mocked(apiRequest).mockImplementation(async () => ({ question: keyless(0), distributionWarnings: [] }));
  const onChanged = vi.fn();
  const screen = await render(<QuestionCard question={keyless(0)} showKey multiVersion={false} onChanged={onChanged} />);

  await expect.element(screen.getByText(/No answer key yet/)).toBeVisible();
  expect(screen.getByText("Correct").query()).toBeNull();

  // Editing one choice directly: no correct answer to pick, and only the choices are sent.
  await screen.getByRole("button", { name: "Edit" }).click();
  await expect.element(screen.getByText(/This question has no answer key yet/)).toBeVisible();
  expect(screen.getByRole("radio").query()).toBeNull();
  await screen.getByLabelText("Choice C", { exact: true }).fill("My own wording for C");
  await screen.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => writes().map(([path, options]) => [path, options?.body])).toContainEqual([
    "/api/questions/question-1",
    {
      choices: [
        { id: "A", text: "Option A" },
        { id: "B", text: "Option B" },
        { id: "C", text: "My own wording for C" },
        { id: "D", text: "Option D" },
      ],
    },
  ]);

  // Its key, on request.
  await screen.getByRole("button", { name: "Generate answer key" }).click();
  await expect.poll(() => writes().map(([path]) => path)).toContain("/api/questions/question-1/solution");
});

test("a key written before the question changed says so, and can be written again", async () => {
  vi.mocked(apiRequest).mockImplementation(async () => ({ question: makeQuestion(1), distributionWarnings: [] }));
  const outdated: Question = { ...makeQuestion(1), needsSolutionReview: true, hasAnswerKey: true, answerKeyOutdated: true };
  const screen = await render(<QuestionCard question={outdated} showKey multiVersion={false} onChanged={() => undefined} />);

  await expect.element(screen.getByText("Answer key out of date")).toBeVisible();
  await expect.element(screen.getByText(/Written before the question last changed/)).toBeVisible();
  await screen.getByRole("button", { name: "Write the key again" }).click();
  await expect.poll(() => writes().map(([path]) => path)).toContain("/api/questions/question-2/solution");
});

function serveExam(exam: () => Exam, keysRunning: () => boolean) {
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method === "POST" && url.endsWith("/answer-keys")) return { run: { id: "keys-1" } };
    if (url.endsWith("/exam")) return exam();
    if (url.endsWith("/ai-status")) return { runs: [] };
    if (url.endsWith("/readiness")) return { canExport: true, requiresConfirmation: false, checks: [], versions: [], durationMinutes: 60 };
    if (url === "/api/runs/keys-1") {
      return { id: "keys-1", kind: "generation", status: keysRunning() ? "running" : "succeeded", stage: "writing_answer_keys" };
    }
    return null;
  });
}

test("all answer keys are generated when the professor asks, with progress, then they are ready", async () => {
  const questions = [keyless(0), keyless(1), { ...makeQuestion(2), hasAnswerKey: true, answerKeyOutdated: false }];
  let exam: Exam = { ...makeExam(0), status: "ready", questions };
  let running = true;
  serveExam(() => exam, () => running);
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  await expect.element(screen.getByText(/1 of 3 questions have an answer key/)).toBeVisible();
  await expect.element(screen.getByText(/Writes the 2 missing keys\. Keys that are up to date are kept\./)).toBeVisible();
  await screen.getByRole("button", { name: "Generate all answer keys" }).click();
  await expect.poll(() => writes().map(([path]) => path)).toContain("/api/exams/exam-1/answer-keys");
  await expect.element(screen.getByText("Writing answer keys: 1 of 3 ready").first()).toBeVisible();

  // The keys arrive, the job ends, and the page says they are ready.
  exam = { ...exam, questions: questions.map((question) => ({ ...makeQuestion(questions.indexOf(question)), hasAnswerKey: true })) };
  running = false;
  await expect.element(screen.getByText("All answer keys are ready"), { timeout: 6000 }).toBeVisible();
  await expect.element(screen.getByText("All 3 answer keys are ready.")).toBeVisible();
});

test("the questions download without any key; the answer key asks first when keys are missing", async () => {
  serveExam(() => ({ ...makeExam(0), status: "ready", questions: [keyless(0), keyless(1)] }), () => false);
  vi.mocked(apiDownload).mockImplementation(async (path) => {
    if (String(path).includes("kind=answer_key") && !String(path).includes("confirm=true")) {
      throw new ApiError("export_needs_confirmation", "Please confirm the export: Q1, Q2 have no answer key yet.", 409);
    }
  });
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  await screen.getByRole("button", { name: "Download PDF" }).click();
  await expect.poll(() => vi.mocked(apiDownload).mock.calls.map(([path]) => path)).toContain(
    "/api/exams/exam-1/export?kind=student&version=all",
  );
  expect(writes().map(([path]) => path)).not.toContain("/api/exams/exam-1/answer-keys");

  await screen.getByLabelText("Download").click();
  await screen.getByRole("option", { name: "Answer key", exact: true }).click();
  await screen.getByRole("button", { name: "Download PDF" }).click();
  await expect.element(screen.getByText("Q1, Q2 have no answer key yet.")).toBeVisible();
  await screen.getByRole("button", { name: "Download anyway" }).click();
  await expect.poll(() => vi.mocked(apiDownload).mock.calls.map(([path]) => path)).toContain(
    "/api/exams/exam-1/export?kind=answer_key&version=all&confirm=true",
  );
});
