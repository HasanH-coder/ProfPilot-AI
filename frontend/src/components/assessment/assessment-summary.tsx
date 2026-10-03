"use client";

import { useId, type ReactNode } from "react";

import type { UploadItem } from "@/components/assessment/use-document-uploads";
import { describeSettings, type AssessmentDraft } from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";
import type { DocumentCategory } from "@/lib/documents/files";
import { cn } from "@/lib/utils";

type AssessmentSummaryProps = {
  draft: AssessmentDraft;
  course: Course | undefined;
  /** The files added on the page. Only uploads Supabase has confirmed are counted. */
  files: UploadItem[];
  className?: string;
  /** Actions shown under the summary, such as the Save button. */
  children: ReactNode;
};

/** A live overview of the assessment settings. Anything left unspecified says so. */
export function AssessmentSummary({
  draft,
  course,
  files,
  className,
  children,
}: AssessmentSummaryProps) {
  const titleId = useId();
  const settings = describeSettings(draft);

  function fileCount(category: DocumentCategory) {
    const count = files.filter((file) => file.category === category && file.documentId).length;
    return count > 0 ? `${count} ${count === 1 ? "file" : "files"}` : undefined;
  }

  const items: { term: string; value?: string; detail?: string | null; empty?: string }[] = [
    { term: "Course", value: course?.code, detail: course?.name },
    { term: "Assessment", value: draft.examName.trim() },
    { term: "Course material", value: fileCount("course_material"), empty: "None" },
    { term: "Previous assessments", value: fileCount("previous_exam"), empty: "None" },
    { term: "Duration", value: settings.duration },
    { term: "Format", value: settings.distribution },
    { term: "Versions", value: settings.versions },
    { term: "Difficulty", value: settings.difficulty },
    { term: "Attachments", value: fileCount("additional_attachment"), empty: "None" },
  ];

  return (
    <aside aria-labelledby={titleId} className={cn("rounded-xl border bg-card p-5", className)}>
      <h2 id={titleId} className="text-base font-semibold tracking-tight">
        Assessment summary
      </h2>
      <dl className="mt-3 divide-y text-sm">
        {items.map(({ term, value, detail, empty = "Not specified" }) => (
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
                <span className="text-muted-foreground">{empty}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-5 flex flex-col gap-3">{children}</div>
    </aside>
  );
}
