import { expect, test } from "vitest";
import { render } from "vitest-browser-react";

import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";

import { professor } from "./fixtures";
import { navigation } from "./mocks/navigation";

test("Sources is available in the sidebar", async () => {
  const screen = await render(<WorkspaceSidebar professor={professor} />);
  const nav = screen.getByRole("navigation", { name: "Workspace" });
  await expect.element(nav.getByRole("link", { name: "Assessments" })).toBeVisible();
  await expect.element(nav.getByRole("link", { name: "Sources" })).toHaveAttribute("href", "/workspace/sources");
});

test("AI Assistant opens Assessment Setup with the assistant, as a normal prefetched link", async () => {
  const screen = await render(<WorkspaceSidebar professor={professor} />);
  const link = screen.getByRole("link", { name: "AI Assistant" });
  await expect.element(link).toHaveAttribute("href", "/workspace/assessments/new?assistant=setup");
  // A <Link> with Next.js's default prefetching (no opt-out).
  await expect.element(link).toHaveAttribute("data-prefetch", "auto");
});

test("AI Assistant shows a spinner while its page is on the way", async () => {
  navigation.linkStatus = { pending: true };
  const screen = await render(<WorkspaceSidebar professor={professor} />);
  const link = screen.getByRole("link", { name: "AI Assistant" });
  await expect.element(link.getByRole("status", { includeHidden: true })).toBeInTheDocument();
});
