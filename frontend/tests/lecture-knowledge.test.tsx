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
    .element(
      screen.getByText(
        /11 of 12 slides have extracted text \(page-level detail unavailable\) · no text on slides 9 · figures not readable on slides 4/,
      ),
    )
    .toBeVisible();
  await expect.element(screen.getByText(/Slides 9 had no extractable text/)).toBeVisible();
});

test("with page-level detail, only substantive slides count and the others are named by kind", async () => {
  vi.mocked(apiRequest).mockResolvedValue({
    ...READY,
    coverage: {
      ...READY.coverage!,
      substantiveUnits: 8,
      pageDetail: true,
      pages: { text: [2, 3, 5, 6, 7, 8], textWithFigures: [4, 10], figureDominated: [11], divider: [1, 12], empty: [9], uncertainMath: [6] },
    },
  });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  await screen.getByRole("button", { name: "View" }).click();
  await expect
    .element(
      screen.getByText(
        "8 of 12 slides have substantive text · figures not readable on slides 4, 10 · mostly figures on slides 11 · title or divider slides 1, 12 · no text on slides 9 · formulas unverified on slides 6",
      ),
    )
    .toBeVisible();
});

test("a lecture PDF's pictures are seen as its questions are written, not left unread", async () => {
  vi.mocked(apiRequest).mockResolvedValue({
    ...READY,
    coverage: { ...READY.coverage!, unit: "page", substantiveUnits: 11, pageDetail: true, pages: { text: [1, 3], textWithFigures: [2, 4] } },
  });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  await screen.getByRole("button", { name: "View" }).click();
  await expect
    .element(screen.getByText(/11 of 12 pages have substantive text · pictures \(seen as questions are written\) on pages 2, 4/))
    .toBeVisible();
});

test("a lecture whose knowledge is missing can be built at once, with no question asked", async () => {
  const confirm = vi.spyOn(window, "confirm");
  let started = false;
  vi.mocked(apiRequest).mockImplementation(async (_path, options) => {
    if ((options as { method?: string } | undefined)?.method === "POST") started = true;
    return { ...READY, state: started ? "building" : "none", topics: [] };
  });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "none", topics: null })} />);
  await screen.getByRole("button", { name: "Build lecture knowledge" }).click();
  await expect
    .poll(() => vi.mocked(apiRequest).mock.calls.filter(([, options]) => (options as { method?: string })?.method === "POST"))
    .toEqual([["/api/documents/lecture-1/knowledge", { method: "POST", freshSession: true }]]);
  await expect.element(screen.getByText("Building lecture knowledge…")).toBeVisible();
  expect(confirm).not.toHaveBeenCalled();
});

test("a failed rebuild says the previous lecture knowledge is still in use", async () => {
  vi.mocked(apiRequest).mockResolvedValue({
    ...READY,
    legacy: true,
    rebuild: { state: "failed", error: "The AI service is busy." },
    extraction: { state: "current", rereadable: true },
  });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  await screen.getByRole("button", { name: "View" }).click();
  await expect
    .element(screen.getByText("Rebuilding failed: The AI service is busy. The previous lecture knowledge is still in use."))
    .toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Retry rebuild" })).toBeVisible();
});

test("open details mark the panel, so its course card can take the full row", async () => {
  vi.mocked(apiRequest).mockResolvedValue(READY);
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  const marked = () => screen.container.querySelector("[data-knowledge-open]");
  expect(marked()).toBeNull();
  await screen.getByRole("button", { name: "View" }).click();
  await expect.poll(marked).not.toBeNull();
  await screen.getByRole("button", { name: "Hide" }).click();
  await expect.poll(marked).toBeNull();
});

test("nothing is shown for a file that isn't read yet", async () => {
  const screen = await render(<LectureKnowledgePanel source={source({ state: "none", topics: null }, "processing")} />);
  expect(screen.getByText(/lecture knowledge/i).query()).toBeNull();
});

test("while Sources prepares a lecture, it shows what's under way until it's done, with nothing to press", async () => {
  let checks = 0;
  vi.mocked(apiRequest).mockImplementation(async () => {
    checks += 1;
    const pictures = { readable: true, unread: [], read: [2, 4], afterBuild: false, error: null };
    if (checks === 1) return { ...READY, preparation: "running", pictures: { ...pictures, state: "reading", read: [] } };
    return { ...READY, preparation: null, pictures: { ...pictures, state: "idle" } };
  });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "none", topics: null })} preparing />);
  // Checked straight away, then every few seconds until it's done.
  await expect.element(screen.getByText("Reading the pictures in this lecture…")).toBeVisible();
  await expect.element(screen.getByText("Lecture knowledge · 2 topics"), { timeout: 6000 }).toBeVisible();
  expect(screen.getByRole("button", { name: /Build|Rebuild|Retry|Try again/ }).query()).toBeNull();
});

test("while Sources is still checking which files to prepare, nothing is offered to press", async () => {
  const screen = await render(<LectureKnowledgePanel source={source({ state: "none", topics: null })} checking />);
  await expect.element(screen.getByText("No lecture knowledge yet")).toBeVisible();
  expect(screen.getByRole("button", { name: "Build lecture knowledge" }).query()).toBeNull();
});

test("coverage names the pages whose pictures were read", async () => {
  vi.mocked(apiRequest).mockResolvedValue({
    ...READY,
    coverage: { ...READY.coverage!, substantiveUnits: 11, pageDetail: true, pages: { text: [2, 3, 4] }, unitsWithReadFigures: [2, 4] },
  });
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  await screen.getByRole("button", { name: "View" }).click();
  await expect.element(screen.getByText(/11 of 12 slides have substantive text · pictures read on slides 2, 4/)).toBeVisible();
});

test("a failed picture reading says why, and is tried again at once", async () => {
  const confirm = vi.spyOn(window, "confirm");
  vi.mocked(apiRequest).mockImplementation(async (_path, options) =>
    (options as { method?: string } | undefined)?.method === "POST"
      ? { state: "reading" }
      : { ...READY, pictures: { state: "idle", readable: true, unread: [5], read: [], afterBuild: false, error: "The AI service is busy." } },
  );
  const screen = await render(<LectureKnowledgePanel source={source({ state: "ready", topics: 2 })} />);
  await screen.getByRole("button", { name: "View" }).click();
  await expect.element(screen.getByText(/Reading the pictures failed: The AI service is busy./)).toBeVisible();
  await screen.getByRole("button", { name: "Try again" }).click();
  await expect
    .poll(() => vi.mocked(apiRequest).mock.calls.filter(([, options]) => (options as { method?: string })?.method === "POST"))
    .toEqual([["/api/documents/lecture-1/figures", { method: "POST", freshSession: true }]]);
  expect(confirm).not.toHaveBeenCalled();
});
