import type { Metadata } from "next";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { PageHeader } from "@/components/layout/page-header";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { getCourses } from "@/lib/courses/queries";

export const metadata: Metadata = { title: "Create assessment" };

export default async function NewAssessmentPage({
  searchParams,
}: PageProps<"/workspace/assessments/new">) {
  await getCurrentProfessor();
  const courses = await getCourses();
  const { assistant } = await searchParams;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ href: "/workspace/assessments", label: "Back to Assessments" }}
        title="Create Assessment"
        highlight="Assessment"
        description="Provide the details and materials for your assessment. Every field is optional. Save your draft and continue later."
      />
      <AssessmentForm
        courses={courses}
        initialAssistantOpen={assistant === "setup"}
      />
    </div>
  );
}
