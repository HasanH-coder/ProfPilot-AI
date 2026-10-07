// Set up with AI by voice: course → assessment name → duration. The call must stay
// connected the whole way. It used to end at the duration question: the form's
// autosave rewrote the address to the draft's edit page, so the next save's
// refresh() opened that page, remounting the form and ending the call.

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { ApiError, apiRequest } from "@/lib/api/client";
import type { AssessmentDraft } from "@/lib/assessments/draft";

import { courses } from "./fixtures";
import { navigate, router } from "./mocks/navigation";
import { installFakeRealtime } from "./mocks/realtime";

const QUESTIONS = ["What type of assessment is it?", "Do you want to specify a duration?", "Do you want to specify the question format?"];

/** A stand-in for the setup tools on the API: applies the change to the setup it is sent. */
function setupTools() {
  const requests: { name: string; setup: AssessmentDraft; addressed: string[] }[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    if (path === "/api/realtime/client-secrets") return { clientSecret: "ek_test", expiresAt: 0, model: "realtime" };
    const name = decodeURIComponent(path.split("/").at(-1) ?? "");
    const body = options?.body as { setup: AssessmentDraft; arguments: Record<string, unknown>; addressed: string[] };
    requests.push({ name, setup: body.setup, addressed: body.addressed });
    const setup = { ...body.setup };
    const changes: Record<string, [keyof AssessmentDraft, unknown, string]> = {
      set_course: ["courseId", body.arguments.course_id, "course"],
      set_assessment_name: ["examName", body.arguments.name, "name"],
      set_duration: ["durationMinutes", body.arguments.minutes, "duration"],
    };
    const [field, value, setting] = changes[name] ?? [null, null, String(body.arguments.setting)];
    if (field) Object.assign(setup, { [field]: value });
    const addressed = [...body.addressed, setting];
    return {
      setup,
      result: { ok: true, changedFields: field ? [field] : [], addressed, nextQuestion: QUESTIONS[addressed.length - 1] },
    };
  });
  return requests;
}

/** The model calls these tools in one reply, as it would after hearing the professor. */
function modelCalls(emit: (event: Record<string, unknown>) => void, ...calls: [string, Record<string, unknown>][]) {
  emit({
    type: "response.done",
    response: {
      output: calls.map(([name, args], index) => ({
        type: "function_call",
        name,
        call_id: `${name}-${index}-${Math.random()}`,
        arguments: JSON.stringify(args),
      })),
    },
  });
}

const responses = (sent: Record<string, unknown>[]) => sent.filter((event) => event.type === "response.create").length;
const outputs = (sent: Record<string, unknown>[]) =>
  sent.filter((event) => (event.item as { type?: string } | undefined)?.type === "function_call_output");
const autosave = () => new Promise((resolve) => setTimeout(resolve, 1200)); // runs 0.8 s after a change

let replaceState: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  // Recorded (and not applied) so a rewrite of the address can't go unnoticed.
  replaceState = vi.spyOn(window.history, "replaceState").mockImplementation(() => undefined);
  navigate("/workspace/assessments/new?assistant=setup");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function startCall() {
  const realtime = installFakeRealtime();
  const requests = setupTools();
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("button", { name: "Start voice call" }).click();
  await expect.element(screen.getByRole("button", { name: "End call" })).toBeVisible();
  return { realtime, requests, screen };
}

test("course, Midterm, then 90 minutes: the call stays connected and the AI is asked to go on after each answer", async () => {
  const { realtime, requests, screen } = await startCall();
  expect(responses(realtime.sent())).toBe(1); // ProfPilot opens the conversation

  modelCalls(realtime.emit, ["set_course", { course_id: "course-1" }]);
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(2));
  await autosave();
  modelCalls(realtime.emit, ["set_assessment_name", { name: "Midterm" }]);
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(3));
  await autosave(); // the moment the call used to end: ProfPilot is asking about the duration
  expect(realtime.connected()).toBe(true);
  modelCalls(realtime.emit, ["set_duration", { minutes: 90 }]);
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(4));
  await autosave();

  // Still connected, and exactly one follow-up response after each tool result.
  expect(realtime.connected()).toBe(true);
  await expect.element(screen.getByRole("button", { name: "End call" })).toBeVisible();
  const sent = realtime.sent();
  expect(outputs(sent)).toHaveLength(3);
  expect(sent.map((event) => event.type).slice(1)).toEqual([
    "conversation.item.create",
    "response.create",
    "conversation.item.create",
    "response.create",
    "conversation.item.create",
    "response.create",
  ]);
  expect(outputs(sent).map((event) => JSON.parse((event.item as { output: string }).output).nextQuestion)).toEqual(QUESTIONS);
  // The form kept every answer; nothing navigated or rewrote the address during the call.
  await expect.element(screen.getByRole("textbox", { name: "Assessment name" })).toHaveValue("Midterm");
  await expect.element(screen.getByText("90 minutes")).toBeInTheDocument();
  expect(replaceState).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
  expect(requests.map((request) => request.addressed)).toEqual([[], ["course"], ["course", "name"]]);
});

test("each tool works on the setup the previous one returned, even within one reply", async () => {
  const { realtime, requests } = await startCall();
  modelCalls(
    realtime.emit,
    ["set_course", { course_id: "course-1" }],
    ["set_assessment_name", { name: "Midterm" }],
    ["set_duration", { minutes: 90 }],
  );
  await vi.waitFor(() => expect(requests).toHaveLength(3));
  expect(requests[1].setup.courseId).toBe("course-1");
  expect([requests[2].setup.courseId, requests[2].setup.examName]).toEqual(["course-1", "Midterm"]);
  expect(requests[2].addressed).toEqual(["course", "name"]);
  // One reply with three tools: three results, then one response.
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(2));
  expect(outputs(realtime.sent())).toHaveLength(3);
});

test("skipping the duration keeps it empty and the call going", async () => {
  const { realtime, requests, screen } = await startCall();
  modelCalls(realtime.emit, ["set_course", { course_id: "course-1" }]);
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(2));
  await autosave();
  modelCalls(realtime.emit, ["set_assessment_name", { name: "Midterm" }]);
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(3));
  await autosave();
  modelCalls(realtime.emit, ["skip_setting", { setting: "duration" }]);
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(4));
  await autosave();

  expect(realtime.connected()).toBe(true);
  expect(requests.at(-1)?.addressed).toEqual(["course", "name"]);
  const last = JSON.parse((outputs(realtime.sent()).at(-1)?.item as { output: string }).output);
  expect(last.addressed).toEqual(["course", "name", "duration"]);
  expect(last.nextQuestion).toBe("Do you want to specify the question format?");
  await expect.element(screen.getByRole("button", { name: "End call" })).toBeVisible();
  expect(screen.getByText("90 minutes").query()).toBeNull();
});

test("a change that fails is shown briefly, reported to developers, and the call goes on", async () => {
  const { realtime, screen } = await startCall();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.mocked(apiRequest).mockRejectedValueOnce(new ApiError("invalid_input", "The duration must be from 1 to 1,440 minutes.", 422));
  modelCalls(realtime.emit, ["set_duration", { minutes: 5000 }]);

  await expect.element(screen.getByRole("alert")).toHaveTextContent("Couldn't update the duration. Try saying it again.");
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(2));
  const output = JSON.parse((outputs(realtime.sent())[0].item as { output: string }).output);
  expect(output).toEqual({ ok: false, error: "The duration must be from 1 to 1,440 minutes." });
  expect(consoleError).toHaveBeenCalledWith(
    "[voice call]",
    expect.objectContaining({ event: "function_call", tool: "set_duration", code: "invalid_input" }),
  );
  expect(realtime.connected()).toBe(true);
});

test("a call the model sent garbled is shown too, and the diagnostic never quotes what was said", async () => {
  const { realtime, screen } = await startCall();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  // Not JSON: reading it fails with an error that would quote the professor's words.
  realtime.emit({
    type: "response.done",
    response: {
      output: [{ type: "function_call", name: "update_professor_prompt", call_id: "garbled", arguments: "Focus on Bayes" }],
    },
  });

  await expect.element(screen.getByRole("alert")).toHaveTextContent("Couldn't update the instructions. Try saying it again.");
  await vi.waitFor(() => expect(responses(realtime.sent())).toBe(2));
  expect(consoleError).toHaveBeenCalledWith(
    "[voice call]",
    expect.objectContaining({ event: "function_call", tool: "update_professor_prompt", message: "Malformed JSON." }),
  );
  expect(JSON.stringify(consoleError.mock.calls)).not.toContain("Bayes");
  expect(realtime.connected()).toBe(true);
});
