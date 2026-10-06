import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ExamBuilder } from "@/components/exam/exam-builder";
import { PageHeader } from "@/components/layout/page-header";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export async function generateMetadata({ params }: PageProps<"/workspace/assessments/[id]/builder">): Promise<Metadata> {
  await getCurrentProfessor();
  const assessment = await getAssessment((await params).id);
  return { title: assessment ? `Build with AI · ${assessmentTitle(assessment.draft.examName)}` : "Assessment not found" };
}

export default async function BuildPage({ params }: PageProps<"/workspace/assessments/[id]/builder">) {
  await getCurrentProfessor();
  const { id } = await params;
  const assessment = await getAssessment(id);
  if (!assessment) notFound();

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        back={{ href: `/workspace/assessments/${id}`, label: assessmentTitle(assessment.draft.examName) }}
        title="Build with AI"
        description="Create the exam question by question with ProfPilot, by voice or by typing. Approve each question before moving on."
      />
      <ExamBuilder assessmentId={id} />
    </div>
  );
}
