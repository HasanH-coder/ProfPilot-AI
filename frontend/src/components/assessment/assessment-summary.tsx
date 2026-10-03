"use client";

import { useId, type ReactNode } from "react";

import { DIFFICULTY_LABELS, type AssessmentDraft } from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";
import { cn } from "@/lib/utils";

type AssessmentSummaryProps = {
  draft: AssessmentDraft;
  course: Course | undefined;
  className?: string;
  /** Actions shown under the summary, such as the Save button. */
  children: ReactNode;
};

/** A live overview of the assessment settings. Anything left unspecified says so. */
export function AssessmentSummary({ draft, course, className, children }: AssessmentSummaryProps) {
  const titleId = useId();
  const { durationMinutes, mcqPercentage, subjectivePercentage, numberOfVersions } = draft;

  const items = [
    { term: "Course", value: course?.code, detail: course?.name },
    { term: "Assessment", value: draft.examName.trim() },
    {
      term: "Duration",
      value: isPositiveWholeNumber(durationMinutes)
        ? `${durationMinutes} ${durationMinutes === 1 ? "minute" : "minutes"}`
        : undefined,
    },
    {
      term: "Format",
      value:
        mcqPercentage !== null && subjectivePercentage !== null
          ? `${mcqPercentage}% MCQ / ${subjectivePercentage}% Subjective`
          : undefined,
    },
    {
      term: "Versions",
      value: isPositiveWholeNumber(numberOfVersions) ? `${numberOfVersions}` : undefined,
    },
    { term: "Difficulty", value: draft.difficulty ? DIFFICULTY_LABELS[draft.difficulty] : undefined },
  ];

  return (
    <aside aria-labelledby={titleId} className={cn("rounded-xl border bg-card p-5", className)}>
      <h2 id={titleId} className="text-base font-semibold tracking-tight">
        Assessment summary
      </h2>
      <dl className="mt-3 divide-y text-sm">
        {items.map(({ term, value, detail }) => (
          <div key={term} className="flex items-baseline justify-between gap-4 py-2.5">
            <dt className="shrink-0 text-muted-foreground">{term}</dt>
            <dd className="min-w-0 text-right">
              {value ? (
                <>
                  <span className="font-medium break-words">{value}</span>
                  {detail && (
                    <span className="block text-xs break-words text-muted-foreground">{detail}</span>
                  )}
                </>
              ) : (
                <span className="text-muted-foreground">Not specified</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-5 flex flex-col gap-3">{children}</div>
    </aside>
  );
}

function isPositiveWholeNumber(value: number | null): value is number {
  return value !== null && Number.isInteger(value) && value > 0;
}
