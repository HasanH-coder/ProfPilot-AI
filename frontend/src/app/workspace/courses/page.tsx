import { BookOpen, Plus } from "lucide-react";
import type { Metadata } from "next";

import { CourseFormDialog } from "@/components/courses/course-form-dialog";
import { CourseList } from "@/components/courses/course-list";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { getCourses } from "@/lib/courses/queries";

export const metadata: Metadata = { title: "Courses" };

export default async function CoursesPage() {
  await getCurrentProfessor();
  const courses = await getCourses();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader title="Courses" description="The courses you teach. Each assessment is created for one of them.">
        {courses.length > 0 && <AddCourseButton />}
      </PageHeader>

      {courses.length > 0 ? (
        <CourseList courses={courses} />
      ) : (
        <Empty className="border border-dashed py-14">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <BookOpen />
            </EmptyMedia>
            <EmptyTitle>No courses yet</EmptyTitle>
            <EmptyDescription>Add the courses you teach to start creating assessments for them.</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <AddCourseButton />
          </EmptyContent>
        </Empty>
      )}
    </div>
  );
}

function AddCourseButton() {
  return (
    <CourseFormDialog
      trigger={
        <Button>
          <Plus />
          Add course
        </Button>
      }
    />
  );
}
