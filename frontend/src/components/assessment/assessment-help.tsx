"use client";

import { Check, Lightbulb, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

export function AssessmentHelp({
  onOpenAssistant,
}: {
  onOpenAssistant: () => void;
}) {
  return (
    <aside className="assessment-help" aria-labelledby="assessment-help-title">
      <h2 id="assessment-help-title">
        <Lightbulb className="size-5" aria-hidden="true" />
        Need help?
      </h2>
      <p>
        Describe the assessment you have in mind. ProfPilot can help fill in the
        details by voice or text, then create a plan for your review.
      </p>
      <Button type="button" className="w-full" onClick={onOpenAssistant}>
        <Sparkles />
        Open AI Assistant
      </Button>
      <h3>Features</h3>
      <ul>
        {[
          "Questions from your course materials",
          "Your course style and past exams",
          "Multiple equivalent exam versions",
          "Grading rubrics and solutions",
        ].map((feature) => (
          <li key={feature}>
            <Check className="size-3.5 shrink-0" aria-hidden="true" />
            {feature}
          </li>
        ))}
      </ul>
    </aside>
  );
}
