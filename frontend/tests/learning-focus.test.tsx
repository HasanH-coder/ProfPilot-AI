import { expect, test } from "vitest";
import { page, userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";

import { AssessmentForm, type SavedAssessment } from "@/components/assessment/assessment-form";
import { SpecView } from "@/components/plan/spec-view";
import type { ExamSpec } from "@/lib/api/types";
import { saveAssessmentDraft } from "@/lib/assessments/actions";
import {
  BLOOM_PRESETS,
  checkBloomDistribution,
  CUSTOM_BLOOM_EXAMPLE,
  validateAssessmentDraft,
  type AssessmentDraft,
} from "@/lib/assessments/draft";

import { courses } from "./fixtures";

const SAVED_DRAFT: AssessmentDraft = {
  courseId: null,
  examName: "Midterm",
  durationMinutes: null,
  mcqPercentage: null,
  subjectivePercentage: null,
  numberOfVersions: 1,
  easyPercentage: null,
  mediumPercentage: null,
  hardPercentage: null,
  learningFocus: "custom",
  bloomDistribution: { remember: 20, understand: 20, apply: 20, analyze: 20, evaluate: 10, create: 10 },
  additionalNotes: "",
  professorPrompt: "",
};

function saved(draft: Partial<AssessmentDraft> = {}, extra: Partial<SavedAssessment> = {}): SavedAssessment {
  return { examProjectId: "draft-1", folder: "prof/draft-1", draft: { ...SAVED_DRAFT, ...draft }, files: [], ...extra };
}

const summaryRow = (name: string) =>
  page.getByRole("complementary", { name: "Assessment summary" }).getByText(name, { exact: true });

test("the rules match the backend's: six whole numbers totalling 100, only for custom", () => {
  expect(checkBloomDistribution("custom", CUSTOM_BLOOM_EXAMPLE)).toBeUndefined();
  expect(checkBloomDistribution("custom", { ...CUSTOM_BLOOM_EXAMPLE, create: 10 })).toMatch(/total 100%/);
  expect(checkBloomDistribution("custom", { ...CUSTOM_BLOOM_EXAMPLE, create: 2.5, apply: 32.5 })).toMatch(/whole number/);
  expect(checkBloomDistribution("custom", { ...CUSTOM_BLOOM_EXAMPLE, create: Number.NaN })).toMatch(/whole number/);
  expect(checkBloomDistribution("custom", null)).toMatch(/each of the six/);
  expect(checkBloomDistribution("balanced", CUSTOM_BLOOM_EXAMPLE)).toMatch(/Only a custom/);
  expect(checkBloomDistribution("bespoke", null)).toMatch(/Choose one/);
  for (const preset of Object.values(BLOOM_PRESETS)) {
    expect(Object.values(preset).reduce((sum, share) => sum + share, 0)).toBe(100);
  }
  // A draft saved before the setting existed reads as Automatic and is valid.
  expect(validateAssessmentDraft({ ...SAVED_DRAFT, learningFocus: "automatic", bloomDistribution: null })).toEqual({});
});

test("a new assessment starts on Automatic, with the advanced panel collapsed", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  // Part of Exam design, not a card of its own.
  await expect
    .element(screen.getByRole("region", { name: "Exam design" }).getByRole("group", { name: "Exam Learning Focus" }))
    .toBeVisible();
  await expect.element(screen.getByRole("radio", { name: /^Automatic/ })).toBeChecked();
  await expect.element(screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("slider").query()).toBeNull();
  await expect.element(summaryRow("Automatic")).toBeVisible();

  // Automatic before any plan: an honest pending state, not a made-up split.
  await screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ }).click();
  await expect.element(screen.getByText(/will recommend a distribution/)).toBeVisible();
  expect(screen.getByRole("slider").query()).toBeNull();
});

test("each focus can be chosen, and a preset shows its split at once", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ }).click();
  for (const [name, summary] of [
    ["Balanced", "Balanced"],
    ["Practical & Problem-Solving", "Practical"],
    ["Critical Thinking", "Critical Thinking"],
    ["Automatic", "Automatic"],
  ] as const) {
    await screen.getByRole("radio", { name: new RegExp(`^${name}`) }).click();
    await expect.element(screen.getByRole("radio", { name: new RegExp(`^${name}`) })).toBeChecked();
    await expect.element(summaryRow(summary)).toBeVisible();
  }
  await screen.getByRole("radio", { name: /^Critical Thinking/ }).click();
  const critical = BLOOM_PRESETS.critical;
  await expect.element(screen.getByRole("spinbutton", { name: "Analyze" })).toHaveValue(critical.analyze);
  await expect.element(screen.getByRole("slider", { name: "Evaluate" })).toHaveValue(String(critical.evaluate));
  await expect.element(screen.getByText("Higher-order thinking: 65%")).toBeVisible();
  await expect.element(summaryRow("65% higher-order thinking")).toBeVisible();

  // The cards work from the keyboard like any radio group.
  await screen.getByRole("radio", { name: /^Critical Thinking/ }).click();
  await userEvent.keyboard("{ArrowLeft}");
  await expect.element(screen.getByRole("radio", { name: /^Practical/ })).toBeChecked();
});

test("editing a share makes the split custom; boxes, sliders and the chart stay in step", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("radio", { name: /^Balanced/ }).click();
  await screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ }).click();

  await screen.getByRole("spinbutton", { name: "Create" }).fill("20");
  // Starting from the preset shown, now the professor's own.
  await expect.element(summaryRow("Custom")).toBeVisible();
  expect(screen.getByRole("radio", { checked: true }).query()).toBeNull();
  await expect.element(screen.getByRole("slider", { name: "Create" })).toHaveValue("20");
  await expect.element(screen.getByRole("spinbutton", { name: "Remember" })).toHaveValue(BLOOM_PRESETS.balanced.remember);
  // 110% isn't adjusted silently: it is reported and can't be saved.
  await expect.element(screen.getByText(/Total 110%\. Bloom's percentages must total 100%\./)).toBeVisible();

  // The slider moves the box too (from the keyboard, as its thumb is focused).
  (screen.getByRole("slider", { name: "Create" }).element() as HTMLElement).focus();
  await userEvent.keyboard("{ArrowLeft}");
  await expect.element(screen.getByRole("spinbutton", { name: "Create" })).toHaveValue(19);

  await screen.getByRole("button", { name: "Save draft" }).click();
  expect(saveAssessmentDraft).not.toHaveBeenCalled();

  await screen.getByRole("spinbutton", { name: "Create" }).fill("10");
  await expect.element(screen.getByText("Total 100%")).toBeVisible();
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => saveAssessmentDraft).toHaveBeenCalled();
  expect(saveAssessmentDraft).toHaveBeenCalledWith(
    "draft-1",
    expect.objectContaining({ learningFocus: "custom", bloomDistribution: { ...BLOOM_PRESETS.balanced, create: 10 } }),
  );
});

test("a preset is saved as the choice, without percentages", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("radio", { name: /^Practical/ }).click();
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => saveAssessmentDraft).toHaveBeenCalled();
  expect(saveAssessmentDraft).toHaveBeenCalledWith(
    "draft-1",
    expect.objectContaining({ learningFocus: "practical", bloomDistribution: null }),
  );
});

test("a saved custom split reopens as it was, and survives switching to a preset and back", async () => {
  const screen = await render(<AssessmentForm courses={courses} saved={saved()} />);
  await expect.element(summaryRow("Custom")).toBeVisible();
  await screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ }).click();
  await expect.element(screen.getByRole("spinbutton", { name: "Understand" })).toHaveValue(20);

  await screen.getByRole("radio", { name: /^Balanced/ }).click();
  await expect.element(screen.getByRole("spinbutton", { name: "Understand" })).toHaveValue(BLOOM_PRESETS.balanced.understand);
  await screen.getByRole("button", { name: "Use my custom percentages instead" }).click();
  await expect.element(screen.getByRole("spinbutton", { name: "Understand" })).toHaveValue(20);

  await screen.getByRole("button", { name: "Reset to Recommended" }).click();
  await expect.element(screen.getByRole("radio", { name: /^Automatic/ })).toBeChecked();
});

test("with Automatic, the plan's real recommendation is shown once it exists", async () => {
  const recommendation = { remember: 10, understand: 20, apply: 30, analyze: 20, evaluate: 10, create: 10 };
  const screen = await render(
    <AssessmentForm
      courses={courses}
      saved={saved({ learningFocus: "automatic", bloomDistribution: null }, { bloomRecommendation: recommendation })}
    />,
  );
  await screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ }).click();
  await expect.element(screen.getByText(/Recommended by ProfPilot in your latest plan/)).toBeVisible();
  await expect.element(screen.getByRole("spinbutton", { name: "Apply" })).toHaveValue(30);
  await expect.element(screen.getByText("Higher-order thinking: 40%")).toBeVisible();
});

test("the plan review shows the split, who chose it, and a way to adjust it", async () => {
  const spec: ExamSpec = {
    assessment_title: "Midterm",
    course: null,
    assessment_type: null,
    duration_minutes: null,
    duration_source: null,
    versions: 1,
    total_points: null,
    total_points_source: null,
    question_count_min: null,
    question_count_max: null,
    question_count_source: null,
    question_format: null,
    difficulty: null,
    bloom: {
      focus: "automatic",
      source: "ai_inferred",
      remember_percent: 5,
      understand_percent: 15,
      apply_percent: 30,
      analyze_percent: 30,
      evaluate_percent: 15,
      create_percent: 5,
    },
    sections: [],
    coverage: [],
    learning_emphasis: [],
    question_style: [],
    professor_notes: [],
    professor_preferences_applied: [],
    source_material_guidance: null,
    previous_exam_style_guidance: null,
    constraints: [],
    assumptions: [],
    warnings: [],
    generation_instructions: [],
  };
  const screen = await render(<SpecView spec={spec} editHref="/workspace/assessments/a-1/edit" />);
  await expect.element(screen.getByText(/Recommended by ProfPilot for this assessment/)).toBeVisible();
  await expect.element(screen.getByText("Inferred")).toBeVisible(); // not labelled as the professor's
  await expect.element(screen.getByText("Higher-order thinking: 50%")).toBeVisible();
  await expect.element(screen.getByRole("link", { name: "Adjust" })).toHaveAttribute(
    "href",
    "/workspace/assessments/a-1/edit#learning-focus",
  );
  // Plans made before the setting existed simply say so.
  const old = await render(<SpecView spec={{ ...spec, bloom: undefined }} />);
  await expect.element(old.getByText("Not specified").first()).toBeVisible();
});

test("the section fits a phone screen", async () => {
  await page.viewport(390, 844);
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("button", { name: /Advanced: Bloom's Taxonomy/ }).click();
  await screen.getByRole("button", { name: "Set the percentages yourself" }).click();
  const section = screen.getByRole("group", { name: "Exam Learning Focus" }).element().closest("section")!;
  expect(section.scrollWidth).toBeLessThanOrEqual(section.clientWidth + 1);
  await page.viewport(1280, 900);
});
