import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ExamWorkspace } from "@/components/exam/exam-workspace";
import { PageHeader } from "@/components/layout/page-header";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export async function generateMetadata({ params }: PageProps<"/workspace/assessments/[id]/exam">): Promise<Metadata> {
  await getCurrentProfessor();
  const assessment = await getAssessment((await params).id);
  return { title: assessment ? `Exam · ${assessmentTitle(assessment.draft.examName)}` : "Assessment not found" };
}

export default async function ExamPage({ params }: PageProps<"/workspace/assessments/[id]/exam">) {
  await getCurrentProfessor();
  const { id } = await params;
  const assessment = await getAssessment(id);
  if (!assessment) notFound();
  const course = assessment.course ? [assessment.course.code, assessment.course.name].filter(Boolean).join(" · ") : null;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        back={{ href: `/workspace/assessments/${id}`, label: assessmentTitle(assessment.draft.examName) }}
        title="Exam"
        description={`${course ? `${course}. ` : ""}Review every question, change any of them by hand or with AI, then export the PDFs.`}
      />
      <ExamWorkspace assessmentId={id} />
    </div>
  );
}
