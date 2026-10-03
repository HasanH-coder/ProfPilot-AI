import { Badge } from "@/components/ui/badge";
import { DIFFICULTY_LABELS } from "@/lib/assessments/draft";
import type { AssessmentListItem } from "@/lib/assessments/queries";

const dateFormat = new Intl.DateTimeFormat("en", { dateStyle: "medium" });

/** The professor's saved assessments, newest first. */
export function AssessmentList({ assessments }: { assessments: AssessmentListItem[] }) {
  return (
    <ul className="divide-y rounded-xl border">
      {assessments.map((assessment) => (
        <li
          key={assessment.id}
          className="flex flex-col gap-1 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="truncate font-medium">{assessment.examName}</p>
              <Badge variant="secondary" className="shrink-0">
                {capitalize(assessment.status)}
              </Badge>
            </div>
            <p className="truncate text-sm text-muted-foreground">{describe(assessment)}</p>
          </div>
          <time dateTime={assessment.createdAt} className="shrink-0 text-sm text-muted-foreground">
            {dateFormat.format(new Date(assessment.createdAt))}
          </time>
        </li>
      ))}
    </ul>
  );
}

/** For example: "CMPS 297U · 60 min · 2 versions · Hard". */
function describe(assessment: AssessmentListItem) {
  const { courseCode, durationMinutes, numberOfVersions, difficulty } = assessment;
  return [
    courseCode ?? "No course",
    durationMinutes && `${durationMinutes} min`,
    `${numberOfVersions} ${numberOfVersions === 1 ? "version" : "versions"}`,
    difficulty && (DIFFICULTY_LABELS[difficulty] ?? difficulty),
  ]
    .filter(Boolean)
    .join(" · ");
}

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
