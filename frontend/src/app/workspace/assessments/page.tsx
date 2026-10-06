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
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessments } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export const metadata: Metadata = { title: "Assessments" };

export default async function AssessmentsPage({
  searchParams,
}: PageProps<"/workspace/assessments">) {
  await getCurrentProfessor();
  // Set after an assessment is deleted.
  const { deleted, search } = await searchParams;
  const searchText = (typeof search === "string" ? search : "").trim();
  const allAssessments = await getAssessments();
  const assessments = allAssessments.filter(
    (assessment) =>
      !searchText ||
      [assessmentTitle(assessment.examName), assessment.courseCode]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(searchText.toLowerCase()),
  );

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        title="Assessments"
        description={
          searchText
            ? `Results for “${searchText}”`
            : "Exams and other assessments for your courses."
        }
      >
        {allAssessments.length > 0 && <CreateAssessmentLink />}
      </PageHeader>

      {deleted === "1" && (
        <Alert>
          <CircleCheck />
          <AlertTitle>Assessment deleted</AlertTitle>
          <AlertDescription>
            The assessment and its uploaded files were permanently deleted.
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
            <EmptyTitle>
              {searchText ? "No matching assessments" : "No assessments yet"}
            </EmptyTitle>
            <EmptyDescription>
              {searchText
                ? "Try another name or course code, or clear your search."
                : "Create an assessment to choose its course, format, files, and instructions."}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {searchText ? (
              <Link
                href="/workspace/assessments"
                className={buttonVariants({ variant: "outline" })}
              >
                Clear search
              </Link>
            ) : (
              <CreateAssessmentLink />
            )}
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
