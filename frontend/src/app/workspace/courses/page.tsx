import { BookOpen } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/layout/page-header";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export const metadata: Metadata = { title: "Courses" };

export default async function CoursesPage() {
  // Every workspace page checks the session itself, even when it shows no data yet.
  await getCurrentProfessor();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader title="Courses" description="The courses you teach, in one place." />
      <Empty className="border border-dashed py-14">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <BookOpen />
          </EmptyMedia>
          <EmptyTitle>Course management is coming soon</EmptyTitle>
          <EmptyDescription>
            You&apos;ll be able to add and organize the courses you teach here.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    </div>
  );
}
