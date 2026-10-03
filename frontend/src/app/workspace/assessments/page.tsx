import { CircleCheck, ClipboardCheck, Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { AssessmentList } from "@/components/assessment/assessment-list";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { getAssessments } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export const metadata: Metadata = { title: "Assessments" };

export default async function AssessmentsPage({ searchParams }: PageProps<"/workspace/assessments">) {
  await getCurrentProfessor();
  // Set after saving a draft on the Create assessment page.
  const { saved } = await searchParams;
  const assessments = await getAssessments();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader title="Assessments" description="Exams and other assessments for your courses.">
        <Link href="/workspace/assessments/new" className={buttonVariants()}>
          <Plus />
          Create assessment
        </Link>
      </PageHeader>

      {saved === "1" && (
        <Alert>
          <CircleCheck />
          <AlertTitle>Draft saved</AlertTitle>
          <AlertDescription>
            Your assessment settings are saved. Generating the exam isn&apos;t available yet.
          </AlertDescription>
        </Alert>
      )}

      {assessments.length > 0 ? (
        <AssessmentList assessments={assessments} />
      ) : (
        <Empty className="border border-dashed py-14">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ClipboardCheck />
            </EmptyMedia>
            <EmptyTitle>No assessments yet</EmptyTitle>
            <EmptyDescription>
              Create an assessment to choose its course, format, and instructions.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
    </div>
  );
}
