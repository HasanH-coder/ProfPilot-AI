import { useState } from "react";
import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { CourseSourceSelector } from "@/components/sources/course-source-selector";
import type { CourseSource } from "@/lib/sources/types";

const sources: CourseSource[] = [
  {
    id: "source-ready",
    courseId: "course-1",
    category: "course_material",
    name: "lecture-one.pdf",
    size: 2048,
    status: "ready",
    error: null,
    pageCount: 12,
    title: "Introduction to Machine Learning",
    createdAt: "2026-10-16T10:00:00Z",
  },
  {
    id: "source-pending",
    courseId: "course-1",
    category: "course_material",
    name: "still-processing.pdf",
    size: 1024,
    status: "processing",
    error: null,
    pageCount: null,
    title: null,
    createdAt: "2026-10-16T10:01:00Z",
  },
];

test("selects ready sources by document id and hides unfinished sources", async () => {
  const changed = vi.fn();
  function Harness() {
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    return (
      <CourseSourceSelector
        sources={sources}
        selectedIds={selectedIds}
        onChange={(ids) => {
          setSelectedIds(ids);
          changed(ids);
        }}
      />
    );
  }

  const screen = await render(<Harness />);
  expect(screen.getByText("still-processing.pdf").query()).toBeNull();
  await screen.getByRole("checkbox", { name: "Select lecture-one.pdf" }).click();
  expect(changed).toHaveBeenLastCalledWith(["source-ready"]);
  await expect.element(screen.getByText("1 source selected")).toBeVisible();
});

test("filters sources by generated title", async () => {
  const screen = await render(
    <CourseSourceSelector sources={sources} selectedIds={[]} onChange={() => undefined} />,
  );
  await screen.getByRole("textbox", { name: "Search course sources" }).fill("machine");
  await expect.element(screen.getByText("Introduction to Machine Learning")).toBeVisible();
  await screen.getByRole("textbox", { name: "Search course sources" }).fill("calculus");
  await expect.element(screen.getByText("No matching sources.")).toBeVisible();
});
