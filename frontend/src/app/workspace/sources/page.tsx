import type { Metadata } from "next";

import { PageHeader } from "@/components/layout/page-header";
import { CourseSourcesLibrary } from "@/components/sources/course-sources-library";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { getCoursesWithSources } from "@/lib/sources/queries";

export const metadata: Metadata = { title: "Sources" };

export default async function SourcesPage() {
  // Together: the session check doesn't hold up the query (RLS scopes it either way).
  const [professor, courses] = await Promise.all([getCurrentProfessor(), getCoursesWithSources()]);

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title="Course Sources"
        highlight="Sources"
        description="Add a course and upload its lectures once. ProfPilot reads and saves them, so every assessment can use them without uploading or reading them again."
      />
      <CourseSourcesLibrary initialCourses={courses} professorId={professor.id} />
    </div>
  );
}
