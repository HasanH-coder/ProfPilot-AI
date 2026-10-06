import type { Metadata } from "next";

import { PageHeader } from "@/components/layout/page-header";
import { PreferencesForm } from "@/components/preferences/preferences-form";
import { getCurrentProfessor } from "@/lib/auth/current-professor";

export const metadata: Metadata = { title: "Preferences" };

export default async function PreferencesPage() {
  await getCurrentProfessor();

  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        title="Preferences"
        description="Tell ProfPilot how you like your assessments, and see what it has learned from the work you accepted."
      />
      <PreferencesForm />
    </div>
  );
}
