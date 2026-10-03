import { Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { AssessmentList } from "@/components/assessment/assessment-list";
import { PageHeader } from "@/components/layout/page-header";
import { LinkPendingIcon } from "@/components/link-pending-icon";
import { buttonVariants } from "@/components/ui/button";
import { getAssessments } from "@/lib/assessments/queries";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export const metadata: Metadata = { title: "Workspace" };

export default async function WorkspaceHomePage() {
  const professor = await getCurrentProfessor();
  const recentAssessments = await getAssessments({ limit: 3 });

  return (
    <div className="flex flex-col gap-12">
      <PageHeader
        title={professor.fullName ? `Welcome, ${professor.fullName}` : "Welcome"}
        description="Set up assessments from your course material, previous exams, and your own instructions. AI exam generation and more assistants for teaching and research are coming next."
      >
        <Link href="/workspace/assessments/new" className={buttonVariants({ size: "lg" })}>
          <LinkPendingIcon>
            <Plus />
          </LinkPendingIcon>
          Create assessment
        </Link>
      </PageHeader>

      {recentAssessments.length > 0 && (
        <section aria-labelledby="recent-assessments" className="flex flex-col gap-4">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="recent-assessments" className="text-base font-semibold tracking-tight">
              Recent assessments
            </h2>
            <Link
              href="/workspace/assessments"
              className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              View all assessments
            </Link>
          </div>
          <AssessmentList assessments={recentAssessments} />
        </section>
      )}
    </div>
  );
}
