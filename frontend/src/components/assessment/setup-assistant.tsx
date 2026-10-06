"use client";

import { AudioLines, Send, X } from "lucide-react";
import { useEffect, useId, useState, type KeyboardEvent } from "react";

import { ConversationLog, type ConversationEntry } from "@/components/ai/conversation-log";
import { VoiceCallControls } from "@/components/ai/voice-call-controls";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { RealtimeSecret, SetupChatReply, SetupToolResult } from "@/lib/api/types";
import type { AssessmentDraft } from "@/lib/assessments/draft";
import { useRealtimeCall, useVoiceSupported } from "@/lib/realtime/use-realtime-call";

type SetupAssistantProps = {
  /** The form as it is right now. */
  getSetup: () => AssessmentDraft;
  /** Puts the assistant's changes into the form (which saves them to the draft). */
  onApply: (setup: AssessmentDraft, changedFields: string[]) => void;
  /** Creates the draft if this is a new assessment; returns its id. */
  ensureDraft: () => Promise<string | null>;
  onCallActiveChange: (active: boolean) => void;
  onClose: () => void;
};

let nextId = 0;
const newId = () => `entry-${++nextId}`;

/**
 * "Set up with AI": the professor talks (or types) and ProfPilot fills in the
 * form, live. Everything stays editable by hand, and nothing is required.
 */
export function SetupAssistant({ getSetup, onApply, ensureDraft, onCallActiveChange, onClose }: SetupAssistantProps) {
  const headingId = useId();
  const inputId = useId();
  const [log, setLog] = useState<ConversationEntry[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const voiceSupported = useVoiceSupported();

  function noteChanges(result: SetupToolResult["result"]) {
    const changed = (result.changedFields as string[] | undefined) ?? [];
    if (changed.length > 0 && typeof result.message === "string") {
      setLog((current) => [...current, { id: newId(), role: "change", text: result.message as string }]);
    }
  }

  const call = useRealtimeCall({
    getClientSecret: async () => {
      const assessmentId = await ensureDraft();
      const secret = await apiRequest<RealtimeSecret>("/api/realtime/client-secrets", {
        method: "POST",
        body: { purpose: "setup", assessmentId, setup: getSetup() },
      });
      return secret.clientSecret;
    },
    onToolCall: async (name, args) => {
      const response = await apiRequest<SetupToolResult>(`/api/setup-assistant/tools/${encodeURIComponent(name)}`, {
        method: "POST",
        body: { setup: getSetup(), arguments: args },
      });
      const changed = (response.result.changedFields as string[] | undefined) ?? [];
      if (changed.length > 0) onApply(response.setup, changed);
      noteChanges(response.result);
      return response.result;
    },
    onTranscript: (entry) => {
      setLog((current) => [
        ...current.filter((item) => item.id !== entry.id),
        { id: entry.id, role: entry.role, text: entry.text, channel: "voice" },
      ]);
    },
  });

  const callActive = call.state === "connected" || call.state === "connecting";
  useEffect(() => {
    onCallActiveChange(callActive);
  }, [callActive, onCallActiveChange]);

  async function startCall() {
    setPreparing(true);
    try {
      await call.start();
    } finally {
      setPreparing(false);
    }
  }

  async function send() {
    const text = message.trim();
    if (!text || sending) return;
    setMessage("");
    setChatError(null);

    if (call.state === "connected") {
      call.sendText(text);
      return;
    }

    const professorEntry: ConversationEntry = { id: newId(), role: "professor", text, channel: "text" };
    const history = [...log.filter((entry) => entry.role !== "change"), professorEntry];
    setLog((current) => [...current, professorEntry]);
    setSending(true);
    try {
      const reply = await apiRequest<SetupChatReply>("/api/setup-assistant/messages", {
        method: "POST",
        body: {
          setup: getSetup(),
          messages: history.slice(-16).map((entry) => ({ role: entry.role, content: entry.text })),
        },
      });
      if (reply.changedFields.length > 0) {
        onApply(reply.setup, reply.changedFields);
        setLog((current) => [
          ...current,
          { id: newId(), role: "change", text: `Updated: ${[...new Set(reply.changedFields.map(fieldLabel))].join(", ")}` },
        ]);
      }
      setLog((current) => [...current, { id: newId(), role: "assistant", text: reply.reply, channel: "text" }]);
    } catch (cause) {
      setChatError(errorMessage(cause));
    } finally {
      setSending(false);
    }
  }

  // Live captions that are still arriving.
  const pending = call.transcript
    .filter((entry) => !entry.final && entry.text.trim())
    .map((entry): ConversationEntry => ({ id: entry.id, role: entry.role, text: entry.text, pending: true }));

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold tracking-tight">
            <AudioLines className="size-4 shrink-0" />
            Set up with AI
          </h2>
          <p className="text-sm text-muted-foreground">
            Describe the assessment in your own words. ProfPilot fills in the form below as you talk; you can change
            anything by hand.
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Close Set up with AI"
          onClick={() => {
            call.end();
            onClose();
          }}
        >
          <X />
        </Button>
      </div>

      {voiceSupported ? (
        <VoiceCallControls call={call} onStart={startCall} starting={preparing} />
      ) : (
        <p className="text-sm text-muted-foreground">
          Voice calls aren&apos;t supported in this browser. You can type below instead.
        </p>
      )}

      {call.error && (
        <Alert variant={call.microphoneBlocked ? "default" : "destructive"}>
          <AlertDescription>{call.error}</AlertDescription>
        </Alert>
      )}
      {call.captionsUnavailable && call.state === "connected" && (
        <p className="text-xs text-muted-foreground">Live captions aren&apos;t available, but ProfPilot can still hear you.</p>
      )}

      <ConversationLog
        entries={[...log, ...pending]}
        emptyText="For example: “A 90-minute midterm, about 30% easy, 40% medium and 30% hard, two versions, focused on lectures 3 to 5.”"
        className="max-h-72 min-h-16"
      />

      {/* Not a <form>: this sits inside the assessment form, and forms can't be nested. */}
      <div role="group" aria-label="Type to ProfPilot" className="flex gap-2">
        <label htmlFor={inputId} className="sr-only">
          Message to ProfPilot
        </label>
        <Input
          id={inputId}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder={call.state === "connected" ? "Type during the call…" : "Type instead of talking…"}
          autoComplete="off"
          maxLength={4000}
          enterKeyHint="send"
          onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
            // Enter sends the message; it must never submit the assessment form.
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          disabled={!message.trim() || sending}
          aria-label="Send message"
          onClick={() => void send()}
        >
          {sending ? <Spinner /> : <Send />}
        </Button>
      </div>
      {chatError && (
        <p role="alert" className="text-sm text-destructive">
          {chatError}
        </p>
      )}
    </section>
  );
}

const FIELD_LABELS: Record<string, string> = {
  courseId: "course",
  examName: "assessment name",
  durationMinutes: "duration",
  mcqPercentage: "question format",
  subjectivePercentage: "question format",
  numberOfVersions: "versions",
  easyPercentage: "difficulty",
  mediumPercentage: "difficulty",
  hardPercentage: "difficulty",
  additionalNotes: "notes",
  professorPrompt: "instructions",
};

function fieldLabel(field: string) {
  return FIELD_LABELS[field] ?? field;
}
