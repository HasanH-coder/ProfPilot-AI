// During a voice call, captions and speaking events arrive many times a second.
// They must update the conversation only: re-rendering the whole form or exam
// for each one made scrolling janky.

import { afterEach, expect, test, vi } from "vitest";
import { render, renderHook } from "vitest-browser-react";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { ExamBuilder } from "@/components/exam/exam-builder";
import { apiRequest } from "@/lib/api/client";
import { useRealtimeCall } from "@/lib/realtime/use-realtime-call";

import { courses, makeExam, makeMessages } from "./fixtures";
import { navigate } from "./mocks/navigation";
import { emitBusyConversation, installFakeRealtime, nextTask } from "./mocks/realtime";

// Counting stand-ins: each render of the exam preview or of the form renders these again.
const renders = vi.hoisted(() => ({ questionCards: 0, formSummaries: 0 }));

vi.mock("@/components/exam/question-card", () => ({
  QuestionCard: ({ question }: { question: { id: string; prompt: string } }) => {
    renders.questionCards += 1;
    return <article id={`question-${question.id}`}>{question.prompt}</article>;
  },
}));

vi.mock("@/components/assessment/assessment-summary", () => ({
  AssessmentSummary: () => {
    renders.formSummaries += 1;
    return <aside>Assessment summary</aside>;
  },
}));

const settle = () => new Promise((resolve) => setTimeout(resolve, 300));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function answerApi() {
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    if (path.endsWith("/exam")) return makeExam(8);
    if (path.endsWith("/builder/messages")) return makeMessages(10);
    if (path.endsWith("/realtime/client-secrets")) return { clientSecret: "ek_test", expiresAt: 0, model: "realtime" };
    return { ok: true, changedQuestionIds: [] };
  });
}

test("captions during a call don't re-render the exam preview", async () => {
  const realtime = installFakeRealtime();
  answerApi();
  const screen = await render(<ExamBuilder assessmentId="assessment-1" />);
  await screen.getByRole("button", { name: "Start voice call" }).click();
  await expect.element(screen.getByRole("button", { name: "End call" })).toBeVisible();
  await settle();

  const before = renders.questionCards;
  await emitBusyConversation(realtime.emit);
  await settle();
  // The captions are shown...
  await expect.element(screen.getByText(/word word word/).first()).toBeInTheDocument();
  // ...and the 8 questions next to them were not rendered again.
  expect(renders.questionCards).toBe(before);
});

test("captions during a call don't re-render the setup form", async () => {
  const realtime = installFakeRealtime();
  answerApi();
  navigate("/workspace/assessments/new?assistant=setup");
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("button", { name: "Start voice call" }).click();
  await expect.element(screen.getByRole("button", { name: "End call" })).toBeVisible();
  await settle();

  const before = renders.formSummaries;
  await emitBusyConversation(realtime.emit);
  await settle();
  await expect.element(screen.getByText(/word word word/).first()).toBeInTheDocument();
  expect(renders.formSummaries).toBe(before);
});

test("a burst of caption words is shown in a few updates, and finished lines are handed on", async () => {
  const realtime = installFakeRealtime();
  const onTranscript = vi.fn();
  let count = 0;
  const { result } = await renderHook(() => {
    count += 1;
    return useRealtimeCall({ getClientSecret: async () => "ek_test", onToolCall: async () => ({}), onTranscript });
  });
  await result.current.start();
  await vi.waitFor(() => expect(result.current.state).toBe("connected"));
  await settle();

  const before = count;
  for (let index = 0; index < 100; index++) {
    realtime.emit({ type: "response.output_audio_transcript.delta", item_id: "line-1", delta: `w${index} ` });
    await nextTask();
  }
  await settle();
  // 100 words arriving one by one: a few renders (at most one per 100 ms), not one per word.
  expect(count - before).toBeLessThan(20);
  expect(result.current.transcript).toHaveLength(1);
  expect(result.current.transcript[0].text.split(" ").filter(Boolean)).toHaveLength(100);

  realtime.emit({ type: "response.output_audio_transcript.done", item_id: "line-1", transcript: "The whole line." });
  await vi.waitFor(() => expect(result.current.transcript).toHaveLength(0));
  expect(onTranscript).toHaveBeenCalledWith(
    expect.objectContaining({ id: "line-1", role: "assistant", text: "The whole line.", final: true }),
  );
  // A late piece of a finished line doesn't bring it back.
  realtime.emit({ type: "response.output_audio_transcript.delta", item_id: "line-1", delta: "late" });
  await settle();
  expect(result.current.transcript).toHaveLength(0);
});
