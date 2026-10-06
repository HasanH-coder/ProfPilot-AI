import { Pencil } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AiWorkflowCard } from "@/components/assessment/ai-workflow-card";
import { AssessmentOverview } from "@/components/assessment/assessment-overview";
import { DeleteAssessmentDialog } from "@/components/assessment/delete-assessment-dialog";
import { PageHeader } from "@/components/layout/page-header";
import { LinkPendingIcon } from "@/components/link-pending-icon";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { assessmentTitle } from "@/lib/assessments/draft";
import { getAssessment } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { timeAgo } from "@/lib/time";

export async function generateMetadata({
  params,
}: PageProps<"/workspace/assessments/[id]">): Promise<Metadata> {
  await getCurrentProfessor();
  const assessment = await getAssessment((await params).id);
  return { title: assessment ? assessmentTitle(assessment.draft.examName) : "Assessment not found" };
}

export default async function AssessmentPage({ params }: PageProps<"/workspace/assessments/[id]">) {
  await getCurrentProfessor();
  const { id } = await params;
  // Row Level Security hides other professors' assessments, so they are "not found" too.
  const assessment = await getAssessment(id);
  if (!assessment) notFound();

  const { course, draft, files } = assessment;
  const courseText = course ? [course.code, course.name].filter(Boolean).join(" · ") : "No course";

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        back={{ href: "/workspace/assessments", label: "Assessments" }}
        title={assessmentTitle(draft.examName)}
        badge={<Badge variant="secondary">Draft</Badge>}
        description={`${courseText} · Updated ${timeAgo(assessment.updatedAt)}`}
      >
        <Link href={`/workspace/assessments/${id}/edit`} className={buttonVariants()}>
          <LinkPendingIcon>
            <Pencil />
          </LinkPendingIcon>
          Edit assessment
        </Link>
        <DeleteAssessmentDialog assessmentId={id} examName={draft.examName} fileCount={files.length} />
      </PageHeader>

      <section aria-labelledby="ai-workflow" className="flex flex-col gap-4">
        <h2 id="ai-workflow" className="border-b pb-3 text-base font-semibold tracking-tight">
          AI assessment
        </h2>
        <AiWorkflowCard assessmentId={id} />
      </section>

      <AssessmentOverview assessment={assessment} />
    </div>
  );
}
