import { Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { buttonVariants } from "@/components/ui/button";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export const metadata: Metadata = { title: "Workspace" };

export default async function WorkspaceHomePage() {
  const professor = await getCurrentProfessor();

  return (
    <PageHeader
      title={professor.fullName ? `Welcome, ${professor.fullName}` : "Welcome"}
      description="ProfPilot AI is your academic workspace. Specialized AI assistants for teaching, research, and communication will appear here as they become available."
    >
      <Link href="/workspace/assessments" className={buttonVariants({ size: "lg" })}>
        <Plus />
        Create assessment
      </Link>
    </PageHeader>
  );
}
