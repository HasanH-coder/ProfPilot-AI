import { GraduationCap } from "lucide-react";

/** ProfPilot AI brand mark and name. */
export function Logo() {
  return (
    <span className="flex items-center gap-2.5 font-semibold tracking-tight">
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <GraduationCap className="size-4.5" />
      </span>
      ProfPilot AI
    </span>
  );
}
