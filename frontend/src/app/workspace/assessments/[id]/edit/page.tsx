import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { PageHeader } from "@/components/layout/page-header";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { getCourses } from "@/lib/courses/queries";

export async function generateMetadata({
  params,
}: PageProps<"/workspace/assessments/[id]/edit">): Promise<Metadata> {
  await getCurrentProfessor();
  const assessment = await getAssessment((await params).id);
  if (!assessment) return { title: "Assessment not found" };
  const examName = assessment.draft.examName.trim();
  return { title: examName ? `Edit ${examName}` : "Edit assessment" };
}

export default async function EditAssessmentPage({
  params,
}: PageProps<"/workspace/assessments/[id]/edit">) {
  const professor = await getCurrentProfessor();
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const [assessment, courses] = await Promise.all([
    getAssessment(id),
    getCourses(),
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
        saved={{
          examProjectId: id,
          folder: `${professor.id}/${id}`,
          draft: assessment.draft,
          files: assessment.files,
        }}
      />
    </div>
  );
}
