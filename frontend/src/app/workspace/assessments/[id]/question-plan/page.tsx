import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { QuestionPlanBuilder } from "@/components/plan/question-plan";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export async function generateMetadata({ params }: PageProps<"/workspace/assessments/[id]/question-plan">): Promise<Metadata> {
  const { id } = await params;
  const [, assessment] = await Promise.all([getCurrentProfessor(), getAssessment(id)]);
  return { title: assessment ? `Question plan · ${assessmentTitle(assessment.draft.examName)}` : "Assessment not found" };
}

export default async function QuestionPlanPage({ params }: PageProps<"/workspace/assessments/[id]/question-plan">) {
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const [, assessment] = await Promise.all([getCurrentProfessor(), getAssessment(id)]);
  if (!assessment) notFound();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        back={{ href: `/workspace/assessments/${id}/plan`, label: "Back to your prompt" }}
        title="Build the exam from the plan"
        description="Read what ProfPilot plans to ask, change anything, write the questions one at a time or all at once, and finish the exam here."
      />
      <QuestionPlanBuilder assessmentId={id} />
    </div>
  );
}
