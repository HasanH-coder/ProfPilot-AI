import { CircleCheck, ClipboardCheck, Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { AssessmentList } from "@/components/assessment/assessment-list";
import { PageHeader } from "@/components/layout/page-header";
import { LinkPendingIcon } from "@/components/link-pending-icon";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
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
  // Set after an assessment is deleted.
  const { deleted } = await searchParams;
  const assessments = await getAssessments();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader title="Assessments" description="Exams and other assessments for your courses.">
        {assessments.length > 0 && <CreateAssessmentLink />}
      </PageHeader>

      {deleted === "1" && (
        <Alert>
          <CircleCheck />
          <AlertTitle>Assessment deleted</AlertTitle>
          <AlertDescription>The assessment and its uploaded files were permanently deleted.</AlertDescription>
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
              Create an assessment to choose its course, format, files, and instructions.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <CreateAssessmentLink />
          </EmptyContent>
        </Empty>
      )}
    </div>
  );
}

function CreateAssessmentLink() {
  return (
    <Link href="/workspace/assessments/new" className={buttonVariants()}>
      <LinkPendingIcon>
        <Plus />
      </LinkPendingIcon>
      Create assessment
    </Link>
  );
}
