import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { ConversationLog, type ConversationEntry } from "@/components/ai/conversation-log";

function entries(count: number, from = 0): ConversationEntry[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `entry-${from + index}`,
    role: (from + index) % 2 ? "assistant" : "professor",
    text: `Message ${from + index}: a line of the conversation long enough to wrap onto a second line in the panel.`,
  }));
}

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => resolve(null)));

function logElement() {
  return document.querySelector<HTMLElement>('[role="log"]')!;
}

test("follows new messages while the professor is at the bottom", async () => {
  const screen = await render(<ConversationLog entries={entries(30)} emptyText="" className="max-h-72" />);
  const log = logElement();
  expect(log.scrollTop + log.clientHeight).toBeGreaterThanOrEqual(log.scrollHeight - 1);
  await screen.rerender(<ConversationLog entries={entries(31)} emptyText="" className="max-h-72" />);
  await nextFrame();
  expect(log.scrollTop + log.clientHeight).toBeGreaterThanOrEqual(log.scrollHeight - 1);
});

test("never pulls the professor back down after they scroll up", async () => {
  const screen = await render(<ConversationLog entries={entries(30)} emptyText="" className="max-h-72" />);
  const log = logElement();
  log.scrollTop = 0;
  log.dispatchEvent(new Event("scroll"));
  for (let count = 31; count < 40; count++) {
    await screen.rerender(<ConversationLog entries={entries(count)} emptyText="" className="max-h-72" />);
  }
  await nextFrame();
  expect(log.scrollTop).toBe(0);
});

test("only scrolls itself, never the page", async () => {
  const page = (items: ConversationEntry[]) => (
    <div>
      <div style={{ height: 400 }} />
      <ConversationLog entries={items} emptyText="" className="max-h-72" />
      <div style={{ height: 3000 }} />
    </div>
  );
  const screen = await render(page(entries(10)));
  window.scrollTo(0, 1500);
  for (let count = 11; count < 40; count++) await screen.rerender(page(entries(count)));
  await nextFrame();
  expect(window.scrollY).toBe(1500);
});

test("a long conversation renders the latest messages, and earlier ones on request", async () => {
  const screen = await render(<ConversationLog entries={entries(250)} emptyText="" className="max-h-72" />);
  expect(logElement().querySelectorAll(":scope > div").length).toBe(100);
  await screen.getByRole("button", { name: "Show earlier messages (150)" }).click();
  expect(logElement().querySelectorAll(":scope > div").length).toBe(200);
});

test("its hidden speaker labels stay inside the scroll area (no empty space under the page)", async () => {
  // Screen-reader labels are absolutely positioned; outside the log's containing block
  // they once stretched the page by the whole conversation's height.
  await render(
    <div style={{ position: "relative" }}>
      <ConversationLog entries={entries(200)} emptyText="" className="max-h-72" />
    </div>,
  );
  const log = logElement();
  expect(log.scrollHeight).toBeGreaterThan(2000);
  const contentBottom = log.getBoundingClientRect().bottom + window.scrollY;
  // The page is never shorter than the window; beyond that, nothing below the log.
  expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(Math.max(window.innerHeight, contentBottom + 50));
});

test("shows what ProfPilot is doing, then a short error with Retry", async () => {
  const screen = await render(
    <ConversationLog entries={entries(2)} emptyText="" status={{ kind: "working", text: "Generating the next question…" }} />,
  );
  await expect.element(screen.getByRole("status")).toHaveTextContent("Generating the next question…");
  const retry = vi.fn();
  await screen.rerender(
    <ConversationLog
      entries={entries(2)}
      emptyText=""
      status={{ kind: "error", text: "The next question couldn't be generated.", onRetry: retry }}
    />,
  );
  await expect.element(screen.getByRole("alert")).toHaveTextContent("The next question couldn't be generated.");
  await screen.getByRole("button", { name: "Retry" }).click();
  expect(retry).toHaveBeenCalledOnce();
});

test("captions still arriving aren't announced until they are finished", async () => {
  await render(
    <ConversationLog entries={[{ id: "caption", role: "assistant", text: "Generating that", pending: true }]} emptyText="" />,
  );
  expect(logElement().querySelector('[aria-hidden="true"]')?.textContent).toContain("Generating that");
});
