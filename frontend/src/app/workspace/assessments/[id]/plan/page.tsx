import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PlanReview } from "@/components/plan/plan-review";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export async function generateMetadata({ params }: PageProps<"/workspace/assessments/[id]/plan">): Promise<Metadata> {
  const { id } = await params;
  const [, assessment] = await Promise.all([getCurrentProfessor(), getAssessment(id)]);
  return { title: assessment ? `Improved prompt · ${assessmentTitle(assessment.draft.examName)}` : "Assessment not found" };
}

export default async function PlanPage({ params }: PageProps<"/workspace/assessments/[id]/plan">) {
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const [, assessment] = await Promise.all([getCurrentProfessor(), getAssessment(id)]);
  if (!assessment) notFound();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        back={{ href: `/workspace/assessments/${id}`, label: assessmentTitle(assessment.draft.examName) }}
        title="Review your improved prompt"
        description="ProfPilot read everything you provided and improved your prompt. Read it while your question plan is prepared, edit it if needed, then press Next to go to the plan."
      />
      <PlanReview assessmentId={id} />
    </div>
  );
}
