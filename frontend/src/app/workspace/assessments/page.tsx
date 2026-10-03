import { ClipboardCheck } from "lucide-react";
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

export const metadata: Metadata = { title: "Assessments" };

export default async function AssessmentsPage() {
  // Every workspace page checks the session itself, even when it shows no data yet.
  await getCurrentProfessor();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        title="Assessments"
        description="Exams and other assessments for your courses."
      />
      <Empty className="border border-dashed py-14">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ClipboardCheck />
          </EmptyMedia>
          <EmptyTitle>Assessment creation is coming soon</EmptyTitle>
          <EmptyDescription>
            You&apos;ll be able to create exams and other assessments for your
            courses here.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    </div>
  );
}
