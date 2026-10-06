"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

export type ConversationEntry = {
  id: string;
  role: "professor" | "assistant" | "change";
  text: string;
  /** Still arriving (live captions). */
  pending?: boolean;
  channel?: "voice" | "text";
};

type ConversationLogProps = {
  entries: ConversationEntry[];
  emptyText: string;
  className?: string;
};

/** The conversation with ProfPilot: what was said or typed, and what changed. */
export function ConversationLog({ entries, emptyText, className }: ConversationLogProps) {
  const end = useRef<HTMLDivElement>(null);
  const count = entries.length;
  const lastText = entries.at(-1)?.text;

  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [count, lastText]);

  return (
    <div
      role="log"
      aria-live="polite"
      aria-label="Conversation with ProfPilot"
      className={cn("flex flex-col gap-2 overflow-y-auto", className)}
    >
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        entries.map((entry) =>
          entry.role === "change" ? (
            <p key={entry.id} className="self-center rounded-full bg-muted px-3 py-1 text-center text-xs text-muted-foreground">
              {entry.text}
            </p>
          ) : (
            <div
              key={entry.id}
              className={cn(
                "max-w-[85%] rounded-xl px-3 py-2 text-sm leading-relaxed break-words whitespace-pre-wrap",
                entry.role === "professor"
                  ? "self-end bg-primary text-primary-foreground"
                  : "self-start border bg-card text-card-foreground",
                entry.pending && "opacity-70",
              )}
            >
              <span className="sr-only">{entry.role === "professor" ? "You" : "ProfPilot"}: </span>
              {entry.text}
            </div>
          ),
        )
      )}
      <div ref={end} />
    </div>
  );
}
