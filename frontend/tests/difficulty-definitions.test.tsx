import { expect, test } from "vitest";
import { render } from "vitest-browser-react";

import { AssessmentForm, type SavedAssessment } from "@/components/assessment/assessment-form";
import { saveAssessmentDraft } from "@/lib/assessments/actions";
import {
  checkDifficultyDefinitions,
  DEFAULT_DIFFICULTY_DEFINITIONS,
  DIFFICULTY_DEFINITION_LIMIT,
  isAssessmentDraft,
  savedDifficultyDefinitions,
  validateAssessmentDraft,
  type AssessmentDraft,
} from "@/lib/assessments/draft";

import { courses } from "./fixtures";

const OWN = {
  easy: "Answered straight from the notes.",
  medium: "One short calculation.",
  hard: "Combines two chapters in a new setting.",
};

const SAVED_DRAFT: AssessmentDraft = {
  courseId: null,
  examName: "Quiz",
  durationMinutes: null,
  mcqPercentage: null,
  subjectivePercentage: null,
  numberOfVersions: 1,
  easyPercentage: null,
  mediumPercentage: null,
  hardPercentage: null,
  difficultyDefinitions: null,
  learningFocus: "automatic",
  bloomDistribution: null,
  additionalNotes: "",
  professorPrompt: "",
};

const saved = (draft: Partial<AssessmentDraft> = {}): SavedAssessment => ({
  examProjectId: "draft-1",
  folder: "prof/draft-1",
  draft: { ...SAVED_DRAFT, ...draft },
  files: [],
});

const OPEN = "What do easy, medium and hard mean?";

test("the rules match the backend's: all three, each some text, none too long", () => {
  expect(checkDifficultyDefinitions(null)).toBeUndefined();
  expect(checkDifficultyDefinitions(OWN)).toBeUndefined();
  expect(checkDifficultyDefinitions({ ...OWN, hard: "x".repeat(DIFFICULTY_DEFINITION_LIMIT) })).toBeUndefined();
  expect(checkDifficultyDefinitions({ ...OWN, hard: "x".repeat(DIFFICULTY_DEFINITION_LIMIT + 1) })).toMatch(/characters or fewer/);
  expect(checkDifficultyDefinitions({ ...OWN, easy: "   " })).toMatch(/each of easy, medium and hard/);
  expect(checkDifficultyDefinitions({ easy: "a", medium: "b" })).toMatch(/each of easy, medium and hard/);
  expect(validateAssessmentDraft({ ...SAVED_DRAFT, difficultyDefinitions: { ...OWN, easy: "" } }).difficultyDefinitions).toBeDefined();
  expect(validateAssessmentDraft(SAVED_DRAFT)).toEqual({});
});

test("a draft from a page opened before this setting existed is still accepted and valid", () => {
  const oldDraft: Partial<AssessmentDraft> = { ...SAVED_DRAFT };
  delete oldDraft.difficultyDefinitions;
  expect(isAssessmentDraft(oldDraft)).toBe(true);
  expect(isAssessmentDraft({ ...oldDraft, difficultyDefinitions: "not an object" })).toBe(false);
  expect(validateAssessmentDraft(oldDraft as AssessmentDraft)).toEqual({});
});

test("boxes that still hold the defaults (or nothing) save as null, so the defaults stay ProfPilot's to improve", () => {
  expect(savedDifficultyDefinitions(DEFAULT_DIFFICULTY_DEFINITIONS)).toBeNull();
  expect(savedDifficultyDefinitions({ easy: "", medium: "", hard: "" })).toBeNull();
  expect(savedDifficultyDefinitions({ ...DEFAULT_DIFFICULTY_DEFINITIONS, hard: OWN.hard })).toEqual({
    ...DEFAULT_DIFFICULTY_DEFINITIONS,
    hard: OWN.hard,
  });
});

test("the defaults are shown in the form, with what students should get right", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await expect.element(screen.getByText("ProfPilot's defaults", { exact: true })).toBeVisible();

  await screen.getByText(OPEN).click();
  await expect.element(screen.getByRole("textbox", { name: "Easy", exact: true })).toHaveValue(DEFAULT_DIFFICULTY_DEFINITIONS.easy);
  await expect.element(screen.getByRole("textbox", { name: "Medium", exact: true })).toHaveValue(DEFAULT_DIFFICULTY_DEFINITIONS.medium);
  await expect.element(screen.getByRole("textbox", { name: "Hard", exact: true })).toHaveValue(DEFAULT_DIFFICULTY_DEFINITIONS.hard);

  // Nothing was changed, so nothing custom is saved.
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => saveAssessmentDraft).toHaveBeenCalled();
  expect(saveAssessmentDraft).toHaveBeenCalledWith("draft-1", expect.objectContaining({ difficultyDefinitions: null }));
});

test("rewriting one definition saves all three, and the form says they're the professor's own", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByText(OPEN).click();
  await screen.getByRole("textbox", { name: "Hard", exact: true }).fill(OWN.hard);
  await expect.element(screen.getByText("Your definitions")).toBeVisible();

  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => saveAssessmentDraft).toHaveBeenCalled();
  expect(saveAssessmentDraft).toHaveBeenCalledWith(
    "draft-1",
    expect.objectContaining({ difficultyDefinitions: { ...DEFAULT_DIFFICULTY_DEFINITIONS, hard: OWN.hard } }),
  );
});

test("a blank definition blocks saving and says why", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByText(OPEN).click();
  await screen.getByRole("textbox", { name: "Hard", exact: true }).fill("Something of my own.");
  await screen.getByRole("textbox", { name: "Easy", exact: true }).fill("");
  await screen.getByRole("textbox", { name: "Medium", exact: true }).fill("");

  await expect.element(screen.getByRole("alert").filter({ hasText: /each of easy, medium and hard/ })).toBeVisible();
  await screen.getByRole("button", { name: "Save draft" }).click();
  expect(saveAssessmentDraft).not.toHaveBeenCalled();
});

test("saved definitions reopen as written, and can be put back to the defaults", async () => {
  const screen = await render(<AssessmentForm courses={courses} saved={saved({ difficultyDefinitions: OWN })} />);
  await expect.element(screen.getByText("Your definitions")).toBeVisible();

  await screen.getByText(OPEN).click();
  await expect.element(screen.getByRole("textbox", { name: "Medium", exact: true })).toHaveValue(OWN.medium);

  await screen.getByRole("button", { name: "Use ProfPilot's defaults" }).click();
  await expect.element(screen.getByRole("textbox", { name: "Medium", exact: true })).toHaveValue(DEFAULT_DIFFICULTY_DEFINITIONS.medium);
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => saveAssessmentDraft).toHaveBeenCalled();
  expect(saveAssessmentDraft).toHaveBeenCalledWith("draft-1", expect.objectContaining({ difficultyDefinitions: null }));
});
