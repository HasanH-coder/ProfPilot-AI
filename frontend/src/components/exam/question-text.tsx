import { cn } from "@/lib/utils";

/**
 * Question or answer text as written: paragraphs, line breaks, and ``` code
 * blocks (shown in a monospace box), the same way the PDF prints it.
 */
export function QuestionText({ text, className }: { text: string | null | undefined; className?: string }) {
  if (!text) return null;
  const parts = text.split(/```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```/g);
  return (
    <div className={cn("flex flex-col gap-2 text-sm leading-relaxed", className)}>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <pre key={index} className="overflow-x-auto rounded-lg bg-muted px-3 py-2 font-mono text-[0.8rem] leading-snug">
            <code>{part.replace(/\n$/, "")}</code>
          </pre>
        ) : (
          part
            .split(/\n{2,}/)
            .filter((paragraph) => paragraph.trim())
            .map((paragraph, paragraphIndex) => (
              <p key={`${index}-${paragraphIndex}`} className="break-words whitespace-pre-wrap">
                {paragraph.trim()}
              </p>
            ))
        ),
      )}
    </div>
  );
}
