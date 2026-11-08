import { expect, test, vi } from "vitest";
import { render } from "vitest-browser-react";

import { useDocumentAnalysis } from "@/components/assessment/use-document-analysis";
import { apiRequest } from "@/lib/api/client";

function Probe({ ids }: { ids: string[] }) {
  const analysis = useDocumentAnalysis("a1", ids);
  return (
    <ul>
      {Object.entries(analysis.documents).map(([id, document]) => (
        <li key={id}>{`${id}: ${document.status}`}</li>
      ))}
    </ul>
  );
}

/** Answers the status check with these files and jobs, and records every other request. */
function serve(documents: { id: string; status: string }[], runs: { kind: string; status: string }[]) {
  const posted: string[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    const url = String(path);
    if (options?.method === "POST") {
      posted.push(url);
      return {};
    }
    if (url.endsWith("/ai-status")) return { documents, runs, styleProfile: null };
    return {};
  });
  return posted;
}

test("a file added while a reading job is busy is started at once, not made to wait for the job", async () => {
  // The lecture is being read (that takes about two minutes); the previous exam was added meanwhile.
  const posted = serve(
    [
      { id: "lecture-1", status: "processing" },
      { id: "exam-1", status: "pending" },
    ],
    [{ kind: "document_processing", status: "running" }],
  );
  const screen = await render(<Probe ids={["lecture-1", "exam-1"]} />);

  await expect.element(screen.getByText("exam-1: pending")).toBeVisible();
  await expect.poll(() => posted).toEqual(["/api/documents/exam-1/processing"]);
});

test("with no reading job running, waiting files are read by one job for the assessment, as before", async () => {
  const posted = serve(
    [
      { id: "lecture-1", status: "pending" },
      { id: "exam-1", status: "pending" },
    ],
    [],
  );
  await render(<Probe ids={["lecture-1", "exam-1"]} />);

  await expect.poll(() => posted).toEqual(["/api/assessments/a1/documents/processing"]);
});

test("nothing is started for files that are already being read or ready", async () => {
  const posted = serve(
    [
      { id: "lecture-1", status: "processing" },
      { id: "exam-1", status: "ready" },
    ],
    [{ kind: "document_processing", status: "running" }],
  );
  const screen = await render(<Probe ids={["lecture-1", "exam-1"]} />);

  await expect.element(screen.getByText("lecture-1: processing")).toBeVisible();
  expect(posted).toEqual([]);
});
