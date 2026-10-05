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
        description="Everything is optional. Provide whatever you already know, leave any field blank, and describe the rest in your own words below. ProfPilot will use whatever course material, previous assessments, preferences, and instructions you add to prepare the exam."
      />
      <AssessmentForm courses={courses} />
    </div>
  );
}
