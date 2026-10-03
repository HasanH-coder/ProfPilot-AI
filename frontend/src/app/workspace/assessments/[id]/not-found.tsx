import { FileQuestion } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

/** Shown for an assessment that doesn't exist, was deleted, or belongs to someone else. */
export default function AssessmentNotFound() {
  return (
    <Empty className="border border-dashed py-14">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <FileQuestion />
        </EmptyMedia>
        <EmptyTitle>Assessment not found</EmptyTitle>
        <EmptyDescription>It may have been deleted, or the link may be wrong.</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Link href="/workspace/assessments" className={buttonVariants({ variant: "outline" })}>
          Back to assessments
        </Link>
      </EmptyContent>
    </Empty>
  );
}
