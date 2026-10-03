import { Pencil } from "lucide-react";

import { CourseFormDialog } from "@/components/courses/course-form-dialog";
import { DeleteCourseDialog } from "@/components/courses/delete-course-dialog";
import { Button } from "@/components/ui/button";
import type { Course } from "@/lib/courses/queries";

/** The professor's courses, each with Edit and Delete actions. */
export function CourseList({ courses }: { courses: Course[] }) {
  return (
    <ul className="divide-y rounded-xl border">
      {courses.map((course) => (
        <li key={course.id} className="flex items-center justify-between gap-4 px-4 py-3.5">
          <div className="min-w-0">
            <p className="truncate font-medium">{course.code}</p>
            <p className="truncate text-sm text-muted-foreground">
              {course.name ?? "No course name"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <CourseFormDialog
              course={course}
              trigger={
                <Button variant="ghost" size="sm" aria-label={`Edit ${course.code}`}>
                  <Pencil />
                  <span className="max-sm:sr-only">Edit</span>
                </Button>
              }
            />
            <DeleteCourseDialog course={course} />
          </div>
        </li>
      ))}
    </ul>
  );
}
