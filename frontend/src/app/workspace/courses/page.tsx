import { BookOpen, Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { CourseFormDialog } from "@/components/courses/course-form-dialog";
import { CourseList } from "@/components/courses/course-list";
import { PageHeader } from "@/components/layout/page-header";
import { Button, buttonVariants } from "@/components/ui/button";
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

export default async function CoursesPage({
  searchParams,
}: PageProps<"/workspace/courses">) {
  await getCurrentProfessor();
  const { search } = await searchParams;
  const searchText = (typeof search === "string" ? search : "").trim();
  const allCourses = await getCourses();
  const courses = allCourses.filter(
    (course) =>
      !searchText ||
      [course.code, course.name]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(searchText.toLowerCase()),
  );

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        title="Courses"
        description={
          searchText
            ? `Results for “${searchText}”`
            : "The courses you teach. Choose one when setting up an assessment."
        }
      >
        {allCourses.length > 0 && <AddCourseButton />}
      </PageHeader>

      {courses.length > 0 ? (
        <CourseList courses={courses} />
      ) : (
        <Empty className="border border-dashed py-14">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <BookOpen />
            </EmptyMedia>
            <EmptyTitle>
              {searchText ? "No matching courses" : "No courses yet"}
            </EmptyTitle>
            <EmptyDescription>
              {searchText
                ? "Try another course code or name, or clear your search."
                : "Add the courses you teach to start creating assessments for them."}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {searchText ? (
              <Link
                href="/workspace/courses"
                className={buttonVariants({ variant: "outline" })}
              >
                Clear search
              </Link>
            ) : (
              <AddCourseButton />
            )}
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
