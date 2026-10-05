import type { ReactNode } from "react";

import { DifficultyBar, DifficultySwatch } from "@/components/assessment/difficulty-bar";
import { DocumentList } from "@/components/assessment/document-list";
import { Badge } from "@/components/ui/badge";
import {
  describeSettings,
  DIFFICULTIES,
  DIFFICULTY_LABELS,
  type AssessmentDraft,
  type Difficulty,
} from "@/lib/assessments/draft";
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
  const details: { term: string; value?: ReactNode }[] = [
    { term: "Course", value: course && (course.name ? `${course.code} · ${course.name}` : course.code) },
    { term: "Assessment name", value: draft.examName.trim() },
    { term: "Duration", value: settings.duration },
    { term: "Question distribution", value: settings.distribution },
    { term: "Versions", value: settings.versions },
    {
      term: "Difficulty distribution",
      value: settings.difficulty && <DifficultyDistributionDetail draft={draft} />,
    },
  ];

  return (
    <div className="flex flex-col gap-10">
      <OverviewSection title="Settings">
        <dl className="divide-y rounded-xl border text-sm">
          {details.map(({ term, value }) => (
            <div key={term} className="grid gap-1 px-4 py-3 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">{term}</dt>
              <dd className={value ? "font-medium break-words" : "text-muted-foreground"}>
                {value || "Not specified"}
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

/** The easy, medium and hard shares as a bar, with each percentage written out below it. */
function DifficultyDistributionDetail({ draft }: { draft: AssessmentDraft }) {
  const percentages: Record<Difficulty, number> = {
    easy: draft.easyPercentage ?? 0,
    medium: draft.mediumPercentage ?? 0,
    hard: draft.hardPercentage ?? 0,
  };
  return (
    <div className="flex max-w-xs flex-col gap-3 pt-1">
      <DifficultyBar percentages={percentages} />
      <ul className="flex flex-col gap-1.5">
        {DIFFICULTIES.map((difficulty) => (
          <li key={difficulty} className="flex items-center gap-2">
            <DifficultySwatch difficulty={difficulty} />
            <span className="font-normal">{DIFFICULTY_LABELS[difficulty]}</span>
            <span className="ml-auto tabular-nums">{percentages[difficulty]}%</span>
          </li>
        ))}
      </ul>
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
