import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { CourseSourcesLibrary } from "@/components/sources/course-sources-library";
import { apiRequest } from "@/lib/api/client";
import { removeCourseSource } from "@/lib/sources/actions";
import type { CourseSource, CourseWithSources } from "@/lib/sources/types";

import { professor } from "./fixtures";

const base = { courseId: "course-1", category: "course_material" as const, size: 1000, error: null, pageCount: null, title: null, createdAt: "2026-10-16T10:00:00Z" };
const sources: CourseSource[] = [
  { ...base, id: "ready", name: "Lecture 1.pdf", status: "ready" },
  { ...base, id: "stuck", name: "Lecture 2.pdf", status: "processing" },
  { ...base, id: "waiting", name: "Lecture 3.pdf", status: "pending" },
];
const courses: CourseWithSources[] = [{ id: "course-1", code: "CMPS 297U", name: "Machine Learning", sources }];

test("reading that was interrupted is resumed when Sources opens", async () => {
  await render(<CourseSourcesLibrary initialCourses={courses} professorId={professor.id} />);
  await expect
    .poll(() => vi.mocked(apiRequest).mock.calls.map(([path]) => path).filter((path) => String(path).endsWith("/processing")))
    .toEqual(expect.arrayContaining(["/api/documents/stuck/processing", "/api/documents/waiting/processing"]));
  // A ready source is left alone.
  expect(vi.mocked(apiRequest).mock.calls.some(([path]) => path === "/api/documents/ready/processing")).toBe(false);
});

test("when the AI service can't be reached, an uploaded file says so truthfully and can be retried", async () => {
  vi.mocked(apiRequest).mockRejectedValue(new Error("ProfPilot's AI service can't be reached right now."));
  const waiting = [{ ...base, id: "waiting", name: "Lecture 3.pdf", status: "pending" as const }];
  const screen = await render(
    <CourseSourcesLibrary initialCourses={[{ ...courses[0], sources: waiting }]} professorId={professor.id} />,
  );
  // Not "still uploading" and not "waiting in a queue": the file is saved, reading didn't start.
  await expect.element(screen.getByText(/Lecture 3\.pdf is uploaded, but reading couldn't start/)).toBeVisible();
  await expect.element(screen.getByText("Uploaded · not read yet")).toBeVisible();

  vi.mocked(apiRequest).mockResolvedValue({ status: "processing", error: null, pageCount: null, title: null });
  await screen.getByRole("button", { name: "Retry Lecture 3.pdf" }).click();
  await expect
    .poll(() => vi.mocked(apiRequest).mock.calls.filter(([path]) => path === "/api/documents/waiting/processing").length)
    .toBe(2);
});

test("a source an assessment still uses explains why it can't be removed", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(removeCourseSource).mockResolvedValueOnce({ error: "An assessment uses this source. Unselect it there first." });
  const screen = await render(<CourseSourcesLibrary initialCourses={courses} professorId={professor.id} />);
  await screen.getByRole("button", { name: "Remove Lecture 1.pdf" }).click();
  await expect.element(screen.getByText("An assessment uses this source. Unselect it there first.")).toBeVisible();
  await expect.element(screen.getByText("Lecture 1.pdf")).toBeVisible();
});
