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
  return { title: assessment ? `Edit ${assessment.draft.examName}` : "Assessment not found" };
}

export default async function EditAssessmentPage({
  params,
}: PageProps<"/workspace/assessments/[id]/edit">) {
  const professor = await getCurrentProfessor();
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const [assessment, courses] = await Promise.all([getAssessment(id), getCourses()]);
  if (!assessment) notFound();
  // Only drafts can be changed.
  if (assessment.status !== "draft") redirect(`/workspace/assessments/${id}`);

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        back={{ href: `/workspace/assessments/${id}`, label: assessment.draft.examName }}
        title="Edit assessment"
        description="Only the course and assessment name are required; everything else is optional. Save your draft as you go, or continue to its overview."
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
