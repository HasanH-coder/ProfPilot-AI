import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { LectureKnowledgePanel } from "@/components/sources/lecture-knowledge";
import { apiRequest } from "@/lib/api/client";
import type { LectureKnowledge } from "@/lib/api/types";
import type { CourseSource } from "@/lib/sources/types";

function source(knowledge: CourseSource["knowledge"], status: CourseSource["status"] = "ready"): CourseSource {
  return {
    id: "lecture-1",
    courseId: "course-1",
    category: "course_material",
    name: "Lecture 1.pdf",
    size: 1000,
    status,
    error: null,
    pageCount: 12,
    title: null,
    createdAt: "2026-10-16T10:00:00Z",
    knowledge,
  };
}

const READY: LectureKnowledge = {
  state: "ready",
  error: null,
  title: "Regularization",
  overview: "Why models overfit and how to control it.",
  sections: ["Overfitting", "Regularization"],
  topics: [
    { id: "t1", name: "Overfitting", locations: ["Slide 2"] },
    { id: "t2", name: "L2 regularization", locations: ["Slides 5–7"] },
  ],
  coverage: { unit: "slide", totalUnits: 12, analysedUnits: 11, unitsWithoutText: [9], unitsWithFigures: [4], truncated: false },
  warnings: ["Slides 9 had no extractable text (often a figure, diagram or scan)."],
  usage: { inputTokens: 9000, outputTokens: 5000, modelCalls: 2 },
  estimate: { modelCalls: 2, inputTokens: 9000, outputTokens: 6000 },
};

test("a ready lecture shows its topic count and, on request, a concise overview", async () => {
  vi.mocked(apiRequest).mockResolvedValue(READY);
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  await expect.element(screen.getByText("Lecture knowledge · 2 topics")).toBeVisible();
  expect(apiRequest).not.toHaveBeenCalled(); // nothing is fetched until asked

  await screen.getByRole("button", { name: "View" }).click();
  await expect.element(screen.getByText("L2 regularization")).toBeVisible();
  await expect
    .element(screen.getByText(/11 of 12 slides analysed · no text on slides 9 · figures not readable on slides 4/))
    .toBeVisible();
  await expect.element(screen.getByText(/Slides 9 had no extractable text/)).toBeVisible();
});

test("building asks first, showing the estimated cost, and starts only when confirmed", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.mocked(apiRequest).mockResolvedValue({ ...READY, state: "none", topics: [] });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "none", topics: null })} />);
  await screen.getByRole("button", { name: "Build lecture knowledge" }).click();
  await expect.poll(() => confirm.mock.calls.length).toBe(1);
  expect(confirm.mock.calls[0][0]).toMatch(/2 AI calls, about 9k input and 6k output tokens/);
  expect(vi.mocked(apiRequest).mock.calls.some(([, options]) => (options as { method?: string })?.method === "POST")).toBe(false);

  confirm.mockReturnValue(true);
  await screen.getByRole("button", { name: "Build lecture knowledge" }).click();
  await expect
    .poll(() => vi.mocked(apiRequest).mock.calls.filter(([, options]) => (options as { method?: string })?.method === "POST"))
    .toEqual([["/api/documents/lecture-1/knowledge", { method: "POST" }]]);
  await expect.element(screen.getByText("Building lecture knowledge…")).toBeVisible();
});

test("nothing is shown for a file that isn't read yet", async () => {
  const screen = await render(<LectureKnowledgePanel source={source({ state: "none", topics: null }, "processing")} />);
  expect(screen.getByText(/lecture knowledge/i).query()).toBeNull();
});
