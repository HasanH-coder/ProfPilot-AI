"use client";

import { RotateCcw } from "lucide-react";
import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export type ConversationEntry = {
  id: string;
  role: "professor" | "assistant" | "change";
  text: string;
  /** Still arriving (live captions). */
  pending?: boolean;
  channel?: "voice" | "text";
};

/** What ProfPilot is doing right now (not a message from it), or what just failed. */
export type ConversationStatus =
  | { kind: "working"; text: string }
  | { kind: "error"; text: string; onRetry?: () => void };

type ConversationLogProps = {
  entries: ConversationEntry[];
  emptyText: string;
  className?: string;
  status?: ConversationStatus | null;
};

// The log shows the latest messages; earlier ones appear on request, so a long
// conversation stays quick to update.
const SHOWN_AT_FIRST = 100;
// Within this distance of the bottom, new messages scroll into view.
const NEAR_BOTTOM_PX = 48;

/**
 * The conversation with ProfPilot: what was said or typed, and what changed.
 *
 * New messages scroll into view only when the professor is already at the
 * bottom of the log, and only the log itself scrolls, never the page: reading
 * an earlier message, or scrolling the page during a call, is never interrupted.
 */
export const ConversationLog = memo(function ConversationLog({ entries, emptyText, className, status }: ConversationLogProps) {
  const container = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [shown, setShown] = useState(SHOWN_AT_FIRST);
  const hidden = Math.max(0, entries.length - shown);
  const visible = hidden ? entries.slice(hidden) : entries;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const onScroll = () => {
      atBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight <= NEAR_BOTTOM_PX;
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, []);

  // Before paint, so following the conversation doesn't flicker.
  useLayoutEffect(() => {
    const element = container.current;
    if (element && atBottom.current) element.scrollTop = element.scrollHeight;
  }, [entries, status]);

  return (
    // `relative` makes this the containing block of the screen-reader labels
    // (`sr-only` is absolutely positioned): otherwise they escape the scroll
    // area and stretch the page by the whole conversation's height.
    <div
      ref={container}
      role="log"
      aria-live="polite"
      aria-label="Conversation with ProfPilot"
      className={cn("relative flex flex-col gap-2 overflow-y-auto", className)}
    >
      {hidden > 0 && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="self-center text-xs"
          onClick={() => setShown((count) => count + SHOWN_AT_FIRST)}
        >
          Show earlier messages ({hidden})
        </Button>
      )}
      {entries.length === 0 && !status ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        visible.map((entry) => <LogEntry key={entry.id} entry={entry} />)
      )}
      {status && <StatusLine status={status} />}
    </div>
  );
});

const LogEntry = memo(function LogEntry({ entry }: { entry: ConversationEntry }) {
  if (entry.role === "change") {
    return (
      <p className="self-center rounded-full bg-muted px-3 py-1 text-center text-xs text-muted-foreground">{entry.text}</p>
    );
  }
  return (
    <div
      // Words still arriving aren't announced; the finished line is.
      aria-hidden={entry.pending || undefined}
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
  );
});

function StatusLine({ status }: { status: ConversationStatus }) {
  if (status.kind === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-2 self-start text-sm text-destructive">
        <span>{status.text}</span>
        {status.onRetry && (
          <Button type="button" variant="outline" size="sm" onClick={status.onRetry}>
            <RotateCcw />
            Retry
          </Button>
        )}
      </div>
    );
  }
  return (
    <p role="status" className="flex items-center gap-2 self-start text-sm text-muted-foreground">
      <Spinner className="size-3.5" aria-hidden />
      {status.text}
    </p>
  );
}
