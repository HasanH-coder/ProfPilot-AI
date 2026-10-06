"use client";

// Whether ProfPilot has read each uploaded file yet.
//
// Reading starts in the background as soon as files are uploaded, so it's
// usually done by the time the professor asks for the plan. If the AI service
// is unreachable, nothing is shown: uploads and saving keep working.

import { useCallback, useEffect, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import type { AiStatus, ProcessingStatus } from "@/lib/api/types";

const POLL_MS = 3000;

export type DocumentAnalysis = Record<string, { status: ProcessingStatus; error: string | null }>;

export function useDocumentAnalysis(assessmentId: string | null, documentIds: string[]) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = [...documentIds].sort().join(",");

  useEffect(() => {
    if (!assessmentId || !key) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function check() {
      try {
        const current = await apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`);
        if (cancelled) return;
        setStatus(current);
        const waiting = current.documents.some((document) => document.status === "pending");
        const reading = current.runs.some((run) => run.kind === "document_processing" && run.status === "running");
        if (waiting && !reading) {
          await apiRequest(`/api/assessments/${assessmentId}/documents/processing`, {
            method: "POST",
            body: {},
            freshSession: true,
          }).catch(() => undefined); // already running elsewhere: just keep checking
        }
        const busy = current.documents.some((document) => document.status === "pending" || document.status === "processing");
        if (busy || waiting) timer = setTimeout(check, POLL_MS);
      } catch {
        // The AI service isn't reachable; the form works without it.
      }
    }

    void check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [assessmentId, key, attempt]);

  const retry = useCallback(
    async (documentId: string) => {
      if (!assessmentId) return;
      await apiRequest(`/api/assessments/${assessmentId}/documents/processing`, {
        method: "POST",
        body: { documentId },
        freshSession: true,
      }).catch(() => undefined);
      setAttempt((count) => count + 1);
    },
    [assessmentId],
  );

  const documents: DocumentAnalysis = {};
  for (const document of status?.documents ?? []) {
    documents[document.id] = { status: document.status, error: document.error };
  }
  return { documents, styleProfile: status?.styleProfile ?? null, retry };
}
