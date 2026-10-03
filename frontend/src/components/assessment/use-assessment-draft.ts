"use client";

import { useRef } from "react";

import {
  createAssessmentDraft,
  type CreateDraftResult,
  type DraftLocation,
} from "@/lib/assessments/actions";

/**
 * Gives the draft (exam project) that the form saves to and files belong to.
 * An existing draft is passed in. A new assessment gets its draft from the
 * first upload or the first save, and both reuse that one request, so a new
 * assessment never becomes two drafts.
 */
export function useAssessmentDraft(
  existing: DraftLocation | null,
  details: { courseId: string | null; examName: string },
) {
  const request = useRef<Promise<CreateDraftResult> | null>(null);

  return async function getDraft(): Promise<CreateDraftResult> {
    if (existing) return { draft: existing };
    request.current ??= createAssessmentDraft(details);
    try {
      const result = await request.current;
      // If the draft couldn't be created, the next upload or save tries again.
      if (!result.draft) request.current = null;
      return result;
    } catch (error) {
      request.current = null;
      throw error;
    }
  };
}
