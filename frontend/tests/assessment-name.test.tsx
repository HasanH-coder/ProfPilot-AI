import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, expect, test, vi } from "vitest";
import { page } from "vitest/browser";
import { render } from "vitest-browser-react";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { createAssessmentDraft, saveAssessmentDraft } from "@/lib/assessments/actions";
import type { AssessmentDraft } from "@/lib/assessments/draft";

import { router } from "./mocks/navigation";

const courses = [{ id: "course-1", code: "CMPS 261", name: "Machine Learning" }];
const NAME = "LR Knowledge Test v3 — Evaluation Only";

// The page rendered on the "server" and the React root that hydrates it.
let container: HTMLElement | null = null;
let root: Root | null = null;
afterEach(() => {
  root?.unmount();
  container?.remove();
  root = container = null;
});

const lastSaved = () => vi.mocked(saveAssessmentDraft).mock.calls.at(-1)?.[1];

test("the server-rendered form takes no input until it is hydrated, so nothing typed is lost", async () => {
  container = document.createElement("div");
  container.innerHTML = renderToString(<AssessmentForm courses={courses} />);
  document.body.append(container);
  const form = container.querySelector("form")!;
  const name = container.querySelector<HTMLInputElement>("input[placeholder^='e.g. Midterm']")!;

  // Before hydration the fields can't be focused or typed into, and the browser doesn't restore old values.
  expect(form.inert).toBe(true);
  expect(form.getAttribute("autocomplete")).toBe("off");
  name.focus();
  expect(document.activeElement).not.toBe(name);

  root = hydrateRoot(container, <AssessmentForm courses={courses} />);
  await expect.poll(() => form.inert).toBe(false);
  expect(form.hasAttribute("aria-busy")).toBe(false);

  await page.getByRole("textbox", { name: "Assessment name" }).fill(NAME);
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => lastSaved()?.examName).toBe(NAME);
  expect(vi.mocked(createAssessmentDraft)).toHaveBeenCalledWith({ courseId: null, examName: NAME });
});

test("the name persists through save, reload and later edits", async () => {
  // A new assessment: the name is saved with the draft it creates.
  const screen = await render(<AssessmentForm courses={courses} />);
  // Opened by client-side navigation, the form is usable at once.
  expect(screen.container.querySelector("form")!.inert).toBe(false);
  await screen.getByRole("textbox", { name: "Assessment name" }).fill(NAME);
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(lastSaved).toMatchObject({ examName: NAME });
  expect(vi.mocked(createAssessmentDraft)).toHaveBeenCalledWith({ courseId: null, examName: NAME });
  expect(router.replace).toHaveBeenCalledWith("/workspace/assessments/draft-1/edit", { scroll: false });
  const savedDraft = lastSaved() as AssessmentDraft;
  await screen.unmount();

  // Reloaded from what was saved: the name is there and nothing is unsaved.
  const reopened = await render(
    <AssessmentForm courses={courses} saved={{ examProjectId: "draft-1", folder: "prof/draft-1", draft: savedDraft, files: [] }} />,
  );
  await expect.element(reopened.getByRole("textbox", { name: "Assessment name" })).toHaveValue(NAME);
  await expect.element(reopened.getByText("Saved", { exact: true })).toBeVisible();

  // Editing something else keeps the name in every later save.
  await reopened.getByRole("button", { name: "90 min" }).click();
  await reopened.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(lastSaved).toMatchObject({ examName: NAME, durationMinutes: 90 });
  await expect.element(reopened.getByRole("textbox", { name: "Assessment name" })).toHaveValue(NAME);

  // And a renamed assessment saves the new name.
  await reopened.getByRole("textbox", { name: "Assessment name" }).fill("LR Quiz");
  await reopened.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(lastSaved).toMatchObject({ examName: "LR Quiz", durationMinutes: 90 });
  expect(vi.mocked(createAssessmentDraft)).toHaveBeenCalledTimes(1);
});
