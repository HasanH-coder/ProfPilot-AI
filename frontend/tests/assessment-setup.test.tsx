import { expect, test } from "vitest";
import { render } from "vitest-browser-react";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { createAssessmentDraft } from "@/lib/assessments/actions";

import { courses } from "./fixtures";
import { navigate } from "./mocks/navigation";

test("Create assessment always offers Set up with AI", async () => {
  // Assessments → Create assessment: /workspace/assessments/new, no assistant parameter.
  navigate("/workspace/assessments/new");
  const screen = await render(<AssessmentForm courses={courses} />);
  await expect.element(screen.getByRole("heading", { name: "Set up with AI" })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Start with AI" })).toBeVisible();
  // The panel itself opens only when the professor chooses it.
  expect(screen.getByRole("button", { name: "Start voice call" }).query()).toBeNull();

  await screen.getByRole("button", { name: "Start with AI" }).click();
  await expect.element(screen.getByRole("button", { name: "Close Set up with AI" })).toBeVisible();
  await expect.element(screen.getByRole("textbox", { name: "Message to ProfPilot" })).toHaveFocus();

  await screen.getByRole("button", { name: "Close Set up with AI" }).click();
  await expect.element(screen.getByRole("button", { name: "Start with AI" })).toHaveFocus();
});

test("The AI Assistant link opens the same page with the assistant open", async () => {
  navigate("/workspace/assessments/new?assistant=setup");
  const screen = await render(<AssessmentForm courses={courses} />);
  await expect.element(screen.getByRole("button", { name: "Close Set up with AI" })).toBeVisible();
  await expect.element(screen.getByRole("heading", { name: "Course & Assessment Details" })).toBeVisible();
  // Opening it creates nothing: a draft only exists once the professor saves, uploads or calls.
  expect(createAssessmentDraft).not.toHaveBeenCalled();
});

test("Clicking AI Assistant while already on the page opens the assistant", async () => {
  navigate("/workspace/assessments/new");
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("textbox", { name: "Assessment name" }).fill("Midterm");
  navigate("/workspace/assessments/new?assistant=setup");
  await expect.element(screen.getByRole("button", { name: "Close Set up with AI" })).toBeVisible();
  // Same form, same values: nothing was reloaded or duplicated.
  await expect.element(screen.getByRole("textbox", { name: "Assessment name" })).toHaveValue("Midterm");
  expect(createAssessmentDraft).not.toHaveBeenCalled();
});

test("The Need help card is gone", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await expect.element(screen.getByRole("heading", { name: "Assessment summary" })).toBeVisible();
  expect(screen.getByText("Need help?").query()).toBeNull();
  expect(screen.getByText("Open AI Assistant").query()).toBeNull();
  expect(screen.getByRole("heading", { name: "Features" }).query()).toBeNull();
});
