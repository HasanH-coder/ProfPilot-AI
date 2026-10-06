import type { ReactNode } from "react";

import { DifficultyBar } from "@/components/assessment/difficulty-bar";
import { Badge } from "@/components/ui/badge";
import type { ExamSpec, Provenance, SpecItem } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const PROVENANCE: Record<Provenance, { label: string; title: string; className: string }> = {
  professor: { label: "Your setting", title: "You set or asked for this.", className: "" },
  ai_inferred: {
    label: "Inferred",
    title: "ProfPilot inferred this from what you wrote or uploaded.",
    className: "border-dashed",
  },
  ai_assumption: {
    label: "Assumption",
    title: "ProfPilot assumed this because generation needs it. Change it if it's wrong.",
    className: "border-dashed text-muted-foreground",
  },
};

/** Where a value came from: the professor, or an AI inference or assumption. */
export function ProvenanceBadge({ source }: { source: Provenance | null | undefined }) {
  if (!source) return null;
  const { label, title, className } = PROVENANCE[source];
  return (
    <Badge variant={source === "professor" ? "secondary" : "outline"} className={className} title={title}>
      {label}
    </Badge>
  );
}

function Row({ term, children, source }: { term: string; children: ReactNode; source?: Provenance | null }) {
  return (
    <div className="grid gap-1 px-4 py-3 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{term}</dt>
      <dd className="flex min-w-0 flex-wrap items-start gap-x-2 gap-y-1 text-sm">
        <span className="min-w-0 break-words">{children}</span>
        <ProvenanceBadge source={source} />
      </dd>
    </div>
  );
}

function NotSpecified() {
  return <span className="text-muted-foreground">Not specified</span>;
}

function ItemList({ items }: { items: SpecItem[] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((item, index) => (
        <li key={index} className="flex flex-wrap items-start gap-x-2 gap-y-1">
          <span className="min-w-0 break-words">{item.text}</span>
          <ProvenanceBadge source={item.source} />
        </li>
      ))}
    </ul>
  );
}

const EMPHASIS_LABELS = { high: "High emphasis", normal: "Normal", low: "Light" } as const;

/** The structured assessment plan, with where each decision came from. */
export function SpecView({ spec, className }: { spec: ExamSpec; className?: string }) {
  const count =
    spec.question_count_min && spec.question_count_max
      ? spec.question_count_min === spec.question_count_max
        ? `${spec.question_count_min} questions`
        : `${spec.question_count_min}–${spec.question_count_max} questions`
      : null;
  const lists: { term: string; items: SpecItem[] }[] = [
    { term: "Learning emphasis", items: spec.learning_emphasis },
    { term: "Question style", items: spec.question_style },
    { term: "Your notes", items: spec.professor_notes },
    { term: "Your preferences", items: spec.professor_preferences_applied },
    { term: "Constraints", items: spec.constraints },
  ];

  return (
    <dl className={cn("divide-y rounded-xl border", className)}>
      <Row term="Assessment">
        {[spec.assessment_title, spec.assessment_type].filter(Boolean).join(" · ") || <NotSpecified />}
      </Row>
      <Row term="Course">{spec.course ?? <NotSpecified />}</Row>
      <Row term="Duration" source={spec.duration_source}>
        {spec.duration_minutes ? `${spec.duration_minutes} minutes` : <NotSpecified />}
      </Row>
      <Row term="Question format" source={spec.question_format?.source}>
        {spec.question_format ? (
          `${spec.question_format.mcq_percent}% multiple choice / ${spec.question_format.subjective_percent}% subjective (by marks)`
        ) : (
          <NotSpecified />
        )}
      </Row>
      <Row term="Difficulty" source={spec.difficulty?.source}>
        {spec.difficulty ? (
          <span className="flex flex-col gap-2">
            <span>
              {spec.difficulty.easy_percent}% easy / {spec.difficulty.medium_percent}% medium /{" "}
              {spec.difficulty.hard_percent}% hard (by marks)
            </span>
            <DifficultyBar
              className="max-w-xs"
              percentages={{
                easy: spec.difficulty.easy_percent,
                medium: spec.difficulty.medium_percent,
                hard: spec.difficulty.hard_percent,
              }}
            />
          </span>
        ) : (
          <NotSpecified />
        )}
      </Row>
      <Row term="Versions" source="professor">
        {spec.versions}
      </Row>
      <Row term="Total marks" source={spec.total_points_source}>
        {spec.total_points ?? <NotSpecified />}
      </Row>
      <Row term="Length" source={spec.question_count_source}>
        {count ?? <NotSpecified />}
      </Row>
      {spec.sections.length > 0 && (
        <Row term="Sections">
          <ul className="flex flex-col gap-1">
            {spec.sections.map((section, index) => (
              <li key={index}>
                {section.title}
                {section.weight_percent !== null && (
                  <span className="text-muted-foreground"> · {section.weight_percent}% of marks</span>
                )}
                {section.notes && <span className="text-muted-foreground"> · {section.notes}</span>}
              </li>
            ))}
          </ul>
        </Row>
      )}
      <Row term="Coverage">
        {spec.coverage.length > 0 ? (
          <ul className="flex flex-col gap-1.5">
            {spec.coverage.map((topic, index) => (
              <li key={index} className="flex flex-wrap items-start gap-x-2 gap-y-1">
                <span className="min-w-0 break-words">
                  {topic.topic}
                  {topic.source_documents.length > 0 && (
                    <span className="text-muted-foreground"> · {topic.source_documents.join(", ")}</span>
                  )}
                </span>
                <Badge variant="outline">{EMPHASIS_LABELS[topic.emphasis]}</Badge>
                <ProvenanceBadge source={topic.source} />
              </li>
            ))}
          </ul>
        ) : (
          <NotSpecified />
        )}
      </Row>
      {lists
        .filter((list) => list.items.length > 0)
        .map((list) => (
          <Row key={list.term} term={list.term}>
            <ItemList items={list.items} />
          </Row>
        ))}
      {spec.source_material_guidance && (
        <Row term="Course material">{spec.source_material_guidance}</Row>
      )}
      {spec.previous_exam_style_guidance && (
        <Row term="Previous-exam style">{spec.previous_exam_style_guidance}</Row>
      )}
      {spec.generation_instructions.length > 0 && (
        <Row term="Instructions for writing">
          <ul className="list-disc pl-4">
            {spec.generation_instructions.map((instruction, index) => (
              <li key={index}>{instruction}</li>
            ))}
          </ul>
        </Row>
      )}
    </dl>
  );
}
