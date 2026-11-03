import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { QuestionPlanReview } from "@/components/plan/question-plan-review";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export async function generateMetadata({ params }: PageProps<"/workspace/assessments/[id]/question-plan">): Promise<Metadata> {
  const { id } = await params;
  const [, assessment] = await Promise.all([getCurrentProfessor(), getAssessment(id)]);
  return { title: assessment ? `AI plan · ${assessmentTitle(assessment.draft.examName)}` : "Assessment not found" };
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
        title="ProfPilot's plan"
        description="Here is what ProfPilot plans to ask. Change any question or give your own, then generate the exam."
      />
      <QuestionPlanReview assessmentId={id} />
    </div>
  );
}
