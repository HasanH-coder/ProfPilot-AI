import { expect, test, vi } from "vitest";
import { userEvent } from "vitest/browser";
import { render } from "vitest-browser-react";

import { AssessmentForm, type SavedAssessment } from "@/components/assessment/assessment-form";
import { apiRequest } from "@/lib/api/client";
import type { SetupChatReply } from "@/lib/api/types";
import { saveAssessmentDraft } from "@/lib/assessments/actions";
import type { AssessmentDraft } from "@/lib/assessments/draft";

import { courses } from "./fixtures";

const DRAFT: AssessmentDraft = {
  courseId: null,
  examName: "",
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

function saved(draft: Partial<AssessmentDraft> = {}, files: SavedAssessment["files"] = []): SavedAssessment {
  return { examProjectId: "draft-1", folder: "prof/draft-1", draft: { ...DRAFT, ...draft }, files };
}

type Screen = Awaited<ReturnType<typeof render>>;
/**
 * A card's open/close button, found through its heading (an upload card's file
 * input shares the card's title). Closed, its name also carries the summary.
 */
const toggle = (screen: Screen, title: string) =>
  title.startsWith("Advanced")
    ? screen.getByRole("button", { name: new RegExp(`^${title}`) })
    : screen.getByRole("heading", { name: new RegExp(`^${title}`) }).getByRole("button");
const expectOpen = (screen: Screen, title: string, open: boolean) =>
  expect.element(toggle(screen, title)).toHaveAttribute("aria-expanded", String(open));

test("the essential cards start open and the optional ones closed", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await expectOpen(screen, "Course & Assessment Details", true);
  await expectOpen(screen, "Course material", true);
  await expectOpen(screen, "Previous assessments", false);
  await expectOpen(screen, "Exam design", true);
  await expectOpen(screen, "Additional notes", false);
  await expectOpen(screen, "Additional images or attachments", false);
  await expectOpen(screen, "Tell ProfPilot what you want", true);
  await expectOpen(screen, "Advanced: Bloom's Taxonomy", false);
  // Set up with AI stays a compact banner, not a card.
  await expect.element(screen.getByRole("button", { name: "Start with AI" })).toBeVisible();
  // A closed card hides its content and says what it holds.
  await expect.element(toggle(screen, "Previous assessments")).toHaveAccessibleName(/No previous assessments/);
  expect(screen.getByRole("textbox", { name: "Additional notes" }).query()).toBeNull();
});

test("cards open and close independently, from the mouse or the keyboard, without submitting", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await toggle(screen, "Previous assessments").click();
  await expectOpen(screen, "Previous assessments", true);
  await expectOpen(screen, "Course material", true); // not an accordion

  await toggle(screen, "Exam design").click();
  await toggle(screen, "Additional notes").click();
  await expectOpen(screen, "Exam design", false);
  await expectOpen(screen, "Additional notes", true);
  await expectOpen(screen, "Course & Assessment Details", true);

  // Keyboard: Enter and Space on the focused header.
  const notes = toggle(screen, "Additional notes");
  (notes.element() as HTMLElement).focus();
  await userEvent.keyboard("{Enter}");
  await expectOpen(screen, "Additional notes", false);
  await userEvent.keyboard(" ");
  await expectOpen(screen, "Additional notes", true);
  await expect.element(notes).toHaveFocus();
  // The header controls its panel, and toggling never saves the form.
  const panel = document.getElementById(notes.element().getAttribute("aria-controls")!);
  expect(panel?.contains(screen.getByRole("textbox", { name: "Additional notes" }).element())).toBe(true);
  expect(saveAssessmentDraft).not.toHaveBeenCalled();
});

test("closing and opening cards never changes what was entered", async () => {
  const screen = await render(<AssessmentForm courses={courses} />);
  await screen.getByRole("textbox", { name: "Assessment name" }).fill("Midterm");
  await toggle(screen, "Additional notes").click();
  await screen.getByRole("textbox", { name: "Additional notes" }).fill("Focus on Lectures 4 and 5.");
  await screen.getByRole("textbox", { name: "Tell ProfPilot what you want" }).fill("A practical midterm.");
  await screen.getByRole("button", { name: "90 min" }).click();

  // Custom Bloom's split, with its advanced panel left open.
  await toggle(screen, "Advanced: Bloom's Taxonomy").click();
  await screen.getByRole("button", { name: "Set the percentages yourself" }).click();
  await screen.getByRole("spinbutton", { name: "Remember" }).fill("5");
  await screen.getByRole("spinbutton", { name: "Create" }).fill("10");

  for (let round = 0; round < 3; round++) {
    for (const title of ["Course & Assessment Details", "Exam design", "Additional notes", "Tell ProfPilot what you want"]) {
      await toggle(screen, title).click();
    }
    for (const title of ["Course & Assessment Details", "Exam design", "Additional notes", "Tell ProfPilot what you want"]) {
      await toggle(screen, title).click();
    }
  }

  await expect.element(screen.getByRole("textbox", { name: "Assessment name" })).toHaveValue("Midterm");
  await expect.element(screen.getByRole("textbox", { name: "Additional notes" })).toHaveValue("Focus on Lectures 4 and 5.");
  await expect.element(screen.getByRole("textbox", { name: "Tell ProfPilot what you want" })).toHaveValue("A practical midterm.");
  await expect.element(screen.getByRole("button", { name: "90 min" })).toHaveAttribute("aria-pressed", "true");
  // The nested Bloom's panel kept both its values and its open state.
  await expectOpen(screen, "Advanced: Bloom's Taxonomy", true);
  await expect.element(screen.getByRole("spinbutton", { name: "Remember" })).toHaveValue(5);
  await expect.element(screen.getByRole("spinbutton", { name: "Create" })).toHaveValue(10);

  // Closed cards summarize the real values, and the sidebar keeps up.
  await toggle(screen, "Exam design").click();
  await expect.element(toggle(screen, "Exam design")).toHaveAccessibleName(/90 minutes · 1 version · Custom learning focus/);
  await toggle(screen, "Course & Assessment Details").click();
  await expect.element(toggle(screen, "Course & Assessment Details")).toHaveAccessibleName(/Midterm/);
  await toggle(screen, "Additional notes").click();
  await expect.element(toggle(screen, "Additional notes")).toHaveAccessibleName(/Notes added/);
  // The notes' text itself isn't shown in the header.
  expect(toggle(screen, "Additional notes").element().textContent).not.toContain("Lectures 4 and 5");
  const summary = screen.getByRole("complementary", { name: "Assessment summary" });
  await expect.element(summary.getByText("Midterm", { exact: true })).toBeVisible();
  await expect.element(summary.getByText("90 minutes", { exact: true })).toBeVisible();
  await expect.element(summary.getByText("Custom", { exact: true })).toBeVisible();
});

test("files, and a file that was refused, survive closing their card", async () => {
  const files = [
    { id: "doc-1", category: "course_material" as const, name: "Lecture 1.pdf", size: 1000 },
    { id: "doc-2", category: "course_material" as const, name: "Lecture 2.pdf", size: 1000 },
    { id: "doc-3", category: "previous_exam" as const, name: "Midterm 2025.pdf", size: 1000 },
  ];
  const screen = await render(<AssessmentForm courses={courses} saved={saved({}, files)} />);

  // A file type that isn't allowed is listed with the reason (no upload is attempted).
  const input = document.querySelector<HTMLInputElement>("input[type=file]")!;
  const transfer = new DataTransfer();
  transfer.items.add(new File(["x"], "virus.exe", { type: "application/octet-stream" }));
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  await expect.element(screen.getByText("virus.exe")).toBeVisible();

  await toggle(screen, "Course material").click();
  await expect.element(toggle(screen, "Course material")).toHaveAccessibleName(/2 files uploaded/);
  await expect.element(toggle(screen, "Previous assessments")).toHaveAccessibleName(/1 file uploaded/);
  await toggle(screen, "Course material").click();
  await expect.element(screen.getByText("Lecture 1.pdf")).toBeVisible();
  await expect.element(screen.getByText("Lecture 2.pdf")).toBeVisible();
  await expect.element(screen.getByText("virus.exe")).toBeVisible();
  await toggle(screen, "Previous assessments").click();
  await expect.element(screen.getByText("Midterm 2025.pdf")).toBeVisible();
});

test("saving opens the closed cards that hold errors and focuses the first one", async () => {
  // Saved with a name that's too long (e.g. from before a limit), and then a bad duration.
  const screen = await render(<AssessmentForm courses={courses} saved={saved({ examName: "M".repeat(130) })} />);
  await screen.getByRole("button", { name: "Custom" }).first().click(); // Duration: Custom
  await screen.getByRole("spinbutton").first().fill("0");
  await toggle(screen, "Course & Assessment Details").click();
  await toggle(screen, "Exam design").click();
  // No warnings before the professor tries to save.
  expect(screen.getByText("Needs attention").query()).toBeNull();

  await screen.getByRole("button", { name: "Save draft" }).click();
  await expectOpen(screen, "Course & Assessment Details", true);
  await expectOpen(screen, "Exam design", true);
  await expect.element(screen.getByText("Use 120 characters or fewer.")).toBeVisible();
  await expect.element(screen.getByText(/Enter a whole number of minutes/)).toBeVisible();
  await expect.element(screen.getByRole("textbox", { name: "Assessment name" })).toHaveFocus();
  expect(saveAssessmentDraft).not.toHaveBeenCalled();
  // Closed again, the card still says it needs attention (in words, not only colour).
  await toggle(screen, "Exam design").click();
  await expect.element(toggle(screen, "Exam design")).toHaveAccessibleName(/Needs attention/);
  // Unrelated cards stay as they were.
  await expectOpen(screen, "Additional notes", false);
});

test("Save draft and Continue work with cards closed", async () => {
  vi.mocked(apiRequest).mockResolvedValue({ spec: { status: "approved" }, runs: [] });
  const screen = await render(<AssessmentForm courses={courses} saved={saved({ examName: "Quiz 2" })} />);
  for (const title of ["Course & Assessment Details", "Course material", "Exam design", "Tell ProfPilot what you want"]) {
    await toggle(screen, title).click();
  }
  await screen.getByRole("button", { name: "Save draft" }).click();
  await expect.poll(() => vi.mocked(saveAssessmentDraft).mock.calls.length).toBe(1);
  expect(saveAssessmentDraft).toHaveBeenCalledWith("draft-1", expect.objectContaining({ examName: "Quiz 2" }));

  await screen.getByRole("button", { name: "Continue" }).click();
  await expect.poll(() => vi.mocked(saveAssessmentDraft).mock.calls.length).toBe(2);
  await expect.poll(() => vi.mocked(apiRequest).mock.calls.some(([path]) => String(path).endsWith("/ai-status"))).toBe(true);
});

test("changes from Set up with AI update closed cards without opening them", async () => {
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    if (path === "/api/setup-assistant/messages") {
      const reply: SetupChatReply = {
        reply: "Done.",
        setup: { ...DRAFT, durationMinutes: 120, numberOfVersions: 3, additionalNotes: "Avoid proofs." },
        changedFields: ["durationMinutes", "numberOfVersions", "additionalNotes"],
        finished: false,
        addressed: ["duration", "versions"],
      };
      return reply;
    }
    return undefined;
  });
  const screen = await render(<AssessmentForm courses={courses} />);
  await toggle(screen, "Exam design").click();
  await screen.getByRole("button", { name: "Start with AI" }).click();
  await screen.getByRole("textbox", { name: "Message to ProfPilot" }).fill("Two hours, three versions, no proofs.");
  await screen.getByRole("button", { name: "Send message" }).click();

  await expect.element(toggle(screen, "Exam design")).toHaveAccessibleName(/120 minutes · 3 versions/);
  await expect.element(toggle(screen, "Additional notes")).toHaveAccessibleName(/Notes added/);
  await expectOpen(screen, "Exam design", false);
  await expectOpen(screen, "Additional notes", false);
  const summary = screen.getByRole("complementary", { name: "Assessment summary" });
  await expect.element(summary.getByText("120 minutes", { exact: true })).toBeVisible();
  // Opening the card shows the changed values.
  await toggle(screen, "Exam design").click();
  await expect.element(screen.getByRole("button", { name: "120 min" })).toHaveAttribute("aria-pressed", "true");
});
