import { WorkspaceShell } from "@/components/layout/workspace-shell";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export default async function WorkspaceLayout({
  children,
}: LayoutProps<"/workspace">) {
  const professor = await getCurrentProfessor();
  return <WorkspaceShell professor={professor}>{children}</WorkspaceShell>;
}
