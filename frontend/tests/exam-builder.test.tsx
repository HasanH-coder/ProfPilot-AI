import { afterEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";

import { ExamBuilder } from "@/components/exam/exam-builder";
import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceShell } from "@/components/layout/workspace-shell";
import { apiRequest, apiStream } from "@/lib/api/client";
import type { BuilderChatEvent, Exam, ToolResult } from "@/lib/api/types";

import { deferred, makeExam, makeMessages, professor } from "./fixtures";
import { installFakeRealtime } from "./mocks/realtime";

type Answer = (path: string, options?: { method?: string; body?: unknown }) => unknown;

/** Answers the builder's API requests; `extra` handles the ones a test cares about. */
function answerApi(exam: Exam, messages = makeMessages(4), extra?: Answer) {
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const answer = extra?.(path, options);
    if (answer !== undefined) return answer;
    if (path.endsWith("/exam")) return exam;
    if (path.endsWith("/builder/messages")) return messages;
    if (path.endsWith("/realtime/client-secrets")) return { clientSecret: "ek_test", expiresAt: 0, model: "realtime" };
    return { ok: true, changedQuestionIds: [] };
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("a typed request shows its status at once, and the tool's as soon as it starts", async () => {
  answerApi(makeExam(2));
  const reply = deferred<void>();
  let send: ((event: BuilderChatEvent) => void) | null = null;
  vi.mocked(apiStream).mockImplementation(async (_path, onEvent) => {
    send = onEvent as (event: BuilderChatEvent) => void;
    await reply.promise; // the AI is still working
  });
  const screen = await render(<ExamBuilder assessmentId="assessment-1" />);
  const log = screen.getByRole("log", { name: "Conversation with ProfPilot" });

  await screen.getByRole("textbox", { name: "Message to ProfPilot" }).fill("Generate the next question");
  await screen.getByRole("button", { name: "Send message" }).click();
  // Before the AI has answered anything:
  await expect.element(log.getByRole("status")).toHaveTextContent("ProfPilot is working on it…");
  await expect.element(screen.getByRole("button", { name: "Finish exam" })).toBeDisabled();

  send!({ event: "tool", name: "generate_next_question", arguments: {} });
  await expect.element(log.getByRole("status")).toHaveTextContent("Generating the next question…");

  send!({ event: "done", reply: "Here's question 3.", changedQuestionIds: [], runId: null });
  reply.resolve();
  await expect.element(log.getByText("Here's question 3.")).toBeVisible();
  await expect.element(log.getByRole("status")).not.toBeInTheDocument();
});

test("a failed request says so briefly and can be retried", async () => {
  answerApi(makeExam(1));
  vi.mocked(apiStream).mockImplementation(async (_path, onEvent) => {
    const send = onEvent as (event: BuilderChatEvent) => void;
    send({ event: "error", code: "ai_busy", message: "ProfPilot's AI service is busy right now." });
  });
  const screen = await render(<ExamBuilder assessmentId="assessment-1" />);
  await screen.getByRole("textbox", { name: "Message to ProfPilot" }).fill("Make question 1 harder");
  await screen.getByRole("button", { name: "Send message" }).click();
  await expect.element(screen.getByRole("alert")).toHaveTextContent("ProfPilot's AI service is busy right now.");
  await screen.getByRole("button", { name: "Retry" }).click();
  expect(apiStream).toHaveBeenCalledTimes(2);
});

test("in a call, a tool's status appears before the tool returns", async () => {
  const realtime = installFakeRealtime();
  const tool = deferred<ToolResult>();
  answerApi(makeExam(4), makeMessages(2), (path) => (path.includes("/builder/tools/revise_question") ? tool.promise : undefined));
  const screen = await render(<ExamBuilder assessmentId="assessment-1" />);
  await screen.getByRole("button", { name: "Start voice call" }).click();
  await expect.element(screen.getByRole("button", { name: "End call" })).toBeVisible();

  realtime.emit({
    type: "response.done",
    response: {
      output: [
        {
          type: "function_call",
          name: "revise_question",
          call_id: "call-1",
          arguments: JSON.stringify({ question_number: 4, instruction: "harder", quick_action: "harder" }),
        },
      ],
    },
  });
  const log = screen.getByRole("log", { name: "Conversation with ProfPilot" });
  await expect.element(log.getByRole("status")).toHaveTextContent("Making Question 4 harder…");

  tool.resolve({ ok: true, changedQuestionIds: ["question-4"] });
  await vi.waitFor(() => expect(realtime.sent().at(-1)).toEqual({ type: "response.create" }));
  await expect.element(log.getByRole("status")).not.toBeInTheDocument();
});

test.each([390, 820, 1024, 1280, 1440])("the builder page ends with its content at %ipx (no empty scroll area)", async (width) => {
  await page.viewport(width, 900);
  answerApi(makeExam(1), makeMessages(80));
  const screen = await render(
    <WorkspaceShell professor={professor}>
      <div className="flex flex-col gap-8">
        <PageHeader title="Build with AI" description="Create the exam question by question with ProfPilot." />
        <ExamBuilder assessmentId="assessment-1" />
      </div>
    </WorkspaceShell>,
  );
  await expect.element(screen.getByText("Here's question 79. It tests the median of skewed data.")).toBeInTheDocument();
  const main = document.querySelector("#workspace-content")!;
  const contentBottom = main.getBoundingClientRect().bottom + window.scrollY;
  const root = document.documentElement;
  // A long conversation scrolls inside its panel; the page ends where its content ends.
  expect(root.scrollHeight).toBeLessThanOrEqual(Math.max(window.innerHeight, contentBottom + 2));
  expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
});
