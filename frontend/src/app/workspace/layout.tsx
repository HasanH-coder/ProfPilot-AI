import { WorkspaceMobileHeader } from "@/components/layout/workspace-mobile-header";
import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export default async function WorkspaceLayout({ children }: LayoutProps<"/workspace">) {
  const professor = await getCurrentProfessor();

  return (
    <div className="flex min-h-svh">
      <aside className="sticky top-0 hidden h-svh w-64 shrink-0 border-r bg-sidebar text-sidebar-foreground lg:block">
        <WorkspaceSidebar professor={professor} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <WorkspaceMobileHeader professor={professor} />
        <main className="flex-1 px-6 py-10 md:px-12 md:py-14">
          <div className="max-w-4xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
