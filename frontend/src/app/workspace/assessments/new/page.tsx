import type { Metadata } from "next";

import { AssessmentForm } from "@/components/assessment/assessment-form";
import { PageHeader } from "@/components/layout/page-header";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { getCourses } from "@/lib/courses/queries";

export const metadata: Metadata = { title: "Create assessment" };

export default async function NewAssessmentPage() {
  await getCurrentProfessor();
  const courses = await getCourses();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        back={{ href: "/workspace/assessments", label: "Assessments" }}
        title="Create assessment"
        description="Provide as much or as little guidance as you want. ProfPilot will use your course material, previous assessments, preferences, and instructions to prepare the exam."
      />
      <AssessmentForm courses={courses} />
    </div>
  );
}
