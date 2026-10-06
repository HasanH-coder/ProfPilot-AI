import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PlanReview } from "@/components/plan/plan-review";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export async function generateMetadata({ params }: PageProps<"/workspace/assessments/[id]/plan">): Promise<Metadata> {
  await getCurrentProfessor();
  const assessment = await getAssessment((await params).id);
  return { title: assessment ? `Plan · ${assessmentTitle(assessment.draft.examName)}` : "Assessment not found" };
}

export default async function PlanPage({ params }: PageProps<"/workspace/assessments/[id]/plan">) {
  await getCurrentProfessor();
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const assessment = await getAssessment(id);
  if (!assessment) notFound();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        back={{ href: `/workspace/assessments/${id}`, label: assessmentTitle(assessment.draft.examName) }}
        title="Review the plan"
        description="ProfPilot interpreted everything you provided. Check it, edit it if needed, and approve it before the exam is generated."
      />
      <PlanReview assessmentId={id} />
    </div>
  );
}
