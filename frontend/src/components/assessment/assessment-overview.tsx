import type { ReactNode } from "react";

import { DocumentList } from "@/components/assessment/document-list";
import { Badge } from "@/components/ui/badge";
import { describeSettings } from "@/lib/assessments/draft";
import type { Assessment } from "@/lib/assessments/queries";
import type { DocumentCategory } from "@/lib/documents/files";

const FILE_SECTIONS: { category: DocumentCategory; title: string }[] = [
  { category: "course_material", title: "Course material" },
  { category: "previous_exam", title: "Previous assessments" },
  { category: "additional_attachment", title: "Additional attachments" },
];

/** A read-only overview of everything saved for an assessment. */
export function AssessmentOverview({ assessment }: { assessment: Assessment }) {
  const { course, draft, files } = assessment;
  const settings = describeSettings(draft);
  const details = [
    {
      term: "Course",
      value: course && (course.name ? `${course.code} · ${course.name}` : course.code),
      empty: "No course",
    },
    { term: "Assessment name", value: draft.examName },
    { term: "Duration", value: settings.duration },
    { term: "Question distribution", value: settings.distribution },
    { term: "Versions", value: settings.versions },
    { term: "Difficulty", value: settings.difficulty },
  ];

  return (
    <div className="flex flex-col gap-10">
      <OverviewSection title="Settings">
        <dl className="divide-y rounded-xl border text-sm">
          {details.map(({ term, value, empty = "Not specified" }) => (
            <div key={term} className="grid gap-1 px-4 py-3 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">{term}</dt>
              <dd className={value ? "font-medium break-words" : "text-muted-foreground"}>
                {value || empty}
              </dd>
            </div>
          ))}
        </dl>
      </OverviewSection>

      <OverviewSection title="Additional notes">
        <SavedText text={draft.additionalNotes} empty="No notes." />
      </OverviewSection>

      <OverviewSection title="Professor instructions">
        <SavedText text={draft.professorPrompt} empty="No instructions." />
      </OverviewSection>

      {FILE_SECTIONS.map(({ category, title }) => {
        const sectionFiles = files.filter((file) => file.category === category);
        return (
          <OverviewSection key={category} title={title} count={sectionFiles.length}>
            {sectionFiles.length > 0 ? (
              <DocumentList files={sectionFiles} />
            ) : (
              <p className="text-sm text-muted-foreground">No files.</p>
            )}
          </OverviewSection>
        );
      })}
    </div>
  );
}

function OverviewSection({
  title,
  count,
  children,
}: {
  title: string;
  /** For file sections: how many files there are. */
  count?: number;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="flex items-center gap-2 border-b pb-3 text-base font-semibold tracking-tight">
        {title}
        {count !== undefined && (
          <Badge variant="secondary" aria-label={`${count} ${count === 1 ? "file" : "files"}`}>
            {count}
          </Badge>
        )}
      </h2>
      {children}
    </section>
  );
}

/** Text exactly as the professor wrote it, line breaks included. */
function SavedText({ text, empty }: { text: string; empty: string }) {
  return text.trim() ? (
    <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">{text}</p>
  ) : (
    <p className="text-sm text-muted-foreground">{empty}</p>
  );
}
