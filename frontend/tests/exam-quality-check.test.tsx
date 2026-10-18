import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { ExamWorkspace } from "@/components/exam/exam-workspace";
import { apiRequest } from "@/lib/api/client";
import type { Exam } from "@/lib/api/types";

import { makeExam } from "./fixtures";

function serve(exam: Exam, reviewRunning: boolean) {
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    const url = String(path);
    if (url.endsWith("/exam")) return exam;
    if (url.endsWith("/ai-status")) {
      return { runs: reviewRunning ? [{ id: "run-1", kind: "review", status: "running" }] : [] };
    }
    if (url.startsWith("/api/runs/")) return { id: "run-1", kind: "review", status: "running", stage: "reviewing" };
    return null; // e.g. the final-review readiness, not part of this test
  });
}

test("while the quality check runs, approving stays possible and says what it means", async () => {
  serve({ ...makeExam(2), status: "ready", reviewStatus: "running" }, true);
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  await expect.element(screen.getByText("The quality check is running")).toBeVisible();
  await expect.element(screen.getByText(/A question you approve now is locked/)).toBeVisible();
  const approve = screen.getByRole("button", { name: "Approve" }).first();
  await expect.element(approve).toBeEnabled();
  await expect.element(approve).toHaveAttribute("title", expect.stringMatching(/check can report issues but not fix them/));
});

test("without a running check there is no notice and no extra hint", async () => {
  serve({ ...makeExam(2), status: "ready", reviewStatus: "passed" }, false);
  const screen = await render(<ExamWorkspace assessmentId="assessment-1" />);

  const approve = screen.getByRole("button", { name: "Approve" }).first();
  await expect.element(approve).toBeVisible();
  expect(screen.getByText("The quality check is running").query()).toBeNull();
  expect(approve.element().getAttribute("title")).toBeNull();
});
