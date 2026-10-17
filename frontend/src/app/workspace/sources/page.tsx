import { BookOpen, Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { CourseSourcesLibrary } from "@/components/sources/course-sources-library";
import { buttonVariants } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
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
        description="Build each course’s reusable knowledge library once. ProfPilot reads and indexes these files so future assessments and agents can use the same trusted material."
      />
      {courses.length ? (
        <CourseSourcesLibrary initialCourses={courses} professorId={professor.id} />
      ) : (
        <Empty className="border border-dashed py-14">
          <EmptyHeader>
            <EmptyMedia variant="icon"><BookOpen /></EmptyMedia>
            <EmptyTitle>Add a course first</EmptyTitle>
            <EmptyDescription>Sources are organized by course so material stays scoped to the right teaching context.</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Link href="/workspace/courses" className={buttonVariants()}><Plus /> Add course</Link>
          </EmptyContent>
        </Empty>
      )}
    </div>
  );
}
