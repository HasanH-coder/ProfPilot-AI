import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { AssessmentForm, type SavedAssessment } from "@/components/assessment/assessment-form";
import { saveAssessmentDraft } from "@/lib/assessments/actions";
import type { AssessmentDraft } from "@/lib/assessments/draft";
import { setAssessmentSourceDocuments } from "@/lib/sources/actions";
import type { CourseSource } from "@/lib/sources/types";

const courses = [
  { id: "course-1", code: "CMPS 297U", name: "Machine Learning" },
  { id: "course-2", code: "MATH 201", name: "Calculus" },
];

function source(id: string, courseId: string, name: string, status: CourseSource["status"] = "ready"): CourseSource {
  return {
    id,
    courseId,
    category: "course_material",
    name,
    size: 1000,
    status,
    error: null,
    pageCount: 3,
    title: null,
    createdAt: "2026-10-16T10:00:00Z",
  };
}

const sources = [
  source("lecture-1", "course-1", "Lecture 1.pdf"),
  source("lecture-2", "course-1", "Lecture 2.pdf"),
  source("reading", "course-1", "Reading.pdf", "processing"),
  source("limits", "course-2", "Limits.pdf"),
];

const DRAFT: AssessmentDraft = {
  courseId: "course-1",
  examName: "Quiz 3",
  durationMinutes: null,
  mcqPercentage: null,
  subjectivePercentage: null,
  numberOfVersions: 1,
  easyPercentage: null,
  mediumPercentage: null,
  hardPercentage: null,
  learningFocus: "automatic",
  bloomDistribution: null,
  additionalNotes: "",
  professorPrompt: "",
};

const saved = (sourceIds: string[]): SavedAssessment => ({
  examProjectId: "draft-1",
  folder: "prof/draft-1",
  draft: DRAFT,
  files: [],
  sourceIds,
});

type Screen = Awaited<ReturnType<typeof render>>;
const materialToggle = (screen: Screen) =>
  screen.getByRole("heading", { name: /^Course material/ }).getByRole("button");

test("the course's ready sources can be selected and are saved after the draft", async () => {
  const screen = await render(<AssessmentForm courses={courses} sources={sources} saved={saved([])} />);
  // Only this course's finished sources are offered.
  await expect.element(screen.getByRole("checkbox", { name: "Select Lecture 1.pdf" })).toBeVisible();
  expect(screen.getByText("Reading.pdf").query()).toBeNull();
  expect(screen.getByText("Limits.pdf").query()).toBeNull();

  await screen.getByRole("checkbox", { name: "Select Lecture 2.pdf" }).click();
  const summary = screen.getByRole("complementary", { name: "Assessment summary" });
  await expect.element(summary.getByText("1 source", { exact: true })).toBeVisible();
  await expect.element(screen.getByText("Unsaved changes")).toBeVisible();
  await materialToggle(screen).click();
  await expect.element(materialToggle(screen)).toHaveAccessibleName(/1 source selected/);

  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => vi.mocked(setAssessmentSourceDocuments).mock.calls).toEqual([["draft-1", ["lecture-2"]]]);
  expect(vi.mocked(saveAssessmentDraft).mock.invocationCallOrder[0]).toBeLessThan(
    vi.mocked(setAssessmentSourceDocuments).mock.invocationCallOrder[0],
  );
  await expect.element(screen.getByText("Saved", { exact: true })).toBeVisible();
});

test("a saved selection is restored, and changing the course clears it", async () => {
  const screen = await render(<AssessmentForm courses={courses} sources={sources} saved={saved(["lecture-1"])} />);
  await expect.element(screen.getByRole("checkbox", { name: "Select Lecture 1.pdf" })).toBeChecked();
  await expect.element(screen.getByText("Saved", { exact: true })).toBeVisible();

  await screen.getByRole("combobox", { name: /Course/ }).click();
  await screen.getByRole("option", { name: /MATH 201/ }).click();
  await expect.element(screen.getByRole("checkbox", { name: "Select Limits.pdf" })).not.toBeChecked();
  expect(screen.getByText("Lecture 1.pdf").query()).toBeNull();

  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => vi.mocked(setAssessmentSourceDocuments).mock.calls).toEqual([["draft-1", []]]);
});

test("a selection that isn't one of the course's sources is never sent", async () => {
  // E.g. a source deleted, or one from another course, since the draft was saved.
  const screen = await render(
    <AssessmentForm courses={courses} sources={sources} saved={saved(["lecture-1", "limits", "gone"])} />,
  );
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => vi.mocked(setAssessmentSourceDocuments).mock.calls).toEqual([["draft-1", ["lecture-1"]]]);
});

test("a failed source save is reported like any other save failure", async () => {
  vi.mocked(setAssessmentSourceDocuments).mockResolvedValueOnce({
    error: "The selected sources couldn't be saved. Please try again.",
  });
  const screen = await render(<AssessmentForm courses={courses} sources={sources} saved={saved([])} />);
  await screen.getByRole("checkbox", { name: "Select Lecture 1.pdf" }).click();
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.element(screen.getByText("The selected sources couldn't be saved. Please try again.")).toBeVisible();
  await expect.element(screen.getByText("Save failed")).toBeVisible();
});

test("without sources for the course, the upload card is unchanged", async () => {
  const screen = await render(<AssessmentForm courses={courses} saved={saved([])} />);
  await expect.element(screen.getByRole("heading", { name: /^Course material/ })).toBeVisible();
  expect(screen.getByText("Or use material saved in Sources").query()).toBeNull();
});
