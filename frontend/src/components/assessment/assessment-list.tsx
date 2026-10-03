import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { DIFFICULTY_LABELS } from "@/lib/assessments/draft";
import type { AssessmentListItem } from "@/lib/assessments/queries";
import { timeAgo } from "@/lib/time";

/** The professor's assessments, most recently updated first. Each row opens the assessment. */
export function AssessmentList({ assessments }: { assessments: AssessmentListItem[] }) {
  return (
    <ul className="divide-y rounded-xl border">
      {assessments.map((assessment) => (
        <li
          key={assessment.id}
          className="relative flex flex-col gap-1 px-4 py-3.5 transition-colors first:rounded-t-xl last:rounded-b-xl hover:bg-muted/50 has-[a:focus-visible]:ring-3 has-[a:focus-visible]:ring-ring/50 has-[a:focus-visible]:ring-inset sm:flex-row sm:items-center sm:justify-between sm:gap-4"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {/* after:inset-0 stretches the link over the whole row, so the row is clickable. */}
              <Link
                href={`/workspace/assessments/${assessment.id}`}
                className="truncate font-medium outline-none after:absolute after:inset-0"
              >
                {assessment.examName}
              </Link>
              <Badge variant="secondary" className="shrink-0">
                {capitalize(assessment.status)}
              </Badge>
            </div>
            <p className="truncate text-sm text-muted-foreground">{describe(assessment)}</p>
          </div>
          <p className="shrink-0 text-sm text-muted-foreground">
            Updated <time dateTime={assessment.updatedAt}>{timeAgo(assessment.updatedAt)}</time>
          </p>
        </li>
      ))}
    </ul>
  );
}

/** For example: "CMPS 297U · 60 min · 2 versions · Hard · 3 files". */
function describe(assessment: AssessmentListItem) {
  const { courseCode, durationMinutes, numberOfVersions, difficulty, fileCount } = assessment;
  return [
    courseCode ?? "No course",
    durationMinutes && `${durationMinutes} min`,
    `${numberOfVersions} ${numberOfVersions === 1 ? "version" : "versions"}`,
    difficulty && (DIFFICULTY_LABELS[difficulty] ?? difficulty),
    fileCount > 0 && `${fileCount} ${fileCount === 1 ? "file" : "files"}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
