import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { PageHeader } from "@/components/layout/page-header";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { getCourses } from "@/lib/courses/queries";
import { getAssessmentSourceDocumentIds, getSources } from "@/lib/sources/queries";

export async function generateMetadata({
  params,
}: PageProps<"/workspace/assessments/[id]/edit">): Promise<Metadata> {
  const { id } = await params;
  const [, assessment] = await Promise.all([getCurrentProfessor(), getAssessment(id)]);
  if (!assessment) return { title: "Assessment not found" };
  const examName = assessment.draft.examName.trim();
  return { title: examName ? `Edit ${examName}` : "Edit assessment" };
}

export default async function EditAssessmentPage({
  params,
}: PageProps<"/workspace/assessments/[id]/edit">) {
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const [professor, assessment, courses, sources, sourceIds] = await Promise.all([
    getCurrentProfessor(),
    getAssessment(id),
    getCourses(),
    getSources(),
    getAssessmentSourceDocumentIds(id),
  ]);
  if (!assessment) notFound();
  // Only drafts can be changed.
  if (assessment.status !== "draft") redirect(`/workspace/assessments/${id}`);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ href: "/workspace/assessments", label: "Back to Assessments" }}
        title="Edit Assessment"
        highlight="Assessment"
        description="Provide the details and materials for your assessment. Every field is optional. Save your draft and continue later."
      />
      <AssessmentForm
        courses={courses}
        sources={sources}
        saved={{
          examProjectId: id,
          folder: `${professor.id}/${id}`,
          draft: assessment.draft,
          files: assessment.files,
          sourceIds,
          bloomRecommendation: assessment.bloomRecommendation,
        }}
      />
    </div>
  );
}
