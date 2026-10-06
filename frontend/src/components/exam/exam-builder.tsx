"use client";

import { CheckCheck, MessagesSquare, Send } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { memo, useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";

import { ConversationLog, type ConversationEntry, type ConversationStatus } from "@/components/ai/conversation-log";
import { JobProgress } from "@/components/ai/job-progress";
import { VoiceCallControls } from "@/components/ai/voice-call-controls";
import { builderToolFailure, builderToolStatus } from "@/components/exam/labels";
import { QuestionCard } from "@/components/exam/question-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiRequest, apiStream, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type {
  BuilderChatEvent,
  BuilderChatReply,
  BuilderMessage,
  Exam,
  RealtimeSecret,
  ToolResult,
} from "@/lib/api/types";
import { useRealtimeCall, useVoiceSupported } from "@/lib/realtime/use-realtime-call";
import { cn } from "@/lib/utils";

/** The reloaded exam, keeping the objects of questions that didn't change, so their cards don't re-render. */
function mergeExam(current: Exam | null, next: Exam): Exam {
  if (!current) return next;
  const known = new Map(current.questions.map((question) => [question.id, question]));
  return {
    ...next,
    questions: next.questions.map((question) => {
      const before = known.get(question.id);
      return before && JSON.stringify(before) === JSON.stringify(question) ? before : question;
    }),
  };
}

/**
 * "Build with AI": the professor and ProfPilot write the exam question by
 * question, by voice or text, while the exam preview updates live.
 */
export function ExamBuilder({ assessmentId }: { assessmentId: string }) {
  const router = useRouter();
  const keyId = useId();
  const [exam, setExam] = useState<Exam | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // A refresh that failed after the exam was shown (the exam stays on screen).
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [finishRun, setFinishRun] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  // The conversation is waiting for a tool (e.g. a question being written).
  const [busy, setBusy] = useState(false);
  const [highlighted, setHighlighted] = useState<Set<string>>(() => new Set());
  const [panel, setPanel] = useState<"conversation" | "exam">("conversation");
  const [showKey, setShowKey] = useState(false);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reload = useRef<Promise<void> | null>(null);
  const reloadAgain = useRef(false);
  const loaded = useRef(false);
  const base = `/workspace/assessments/${assessmentId}`;

  // One reload at a time: asking again while one runs reloads once more after it.
  const loadExam = useCallback(() => {
    if (reload.current) {
      reloadAgain.current = true;
      return reload.current;
    }
    const running = (async () => {
      do {
        reloadAgain.current = false;
        try {
          const next = await apiRequest<Exam>(`/api/assessments/${assessmentId}/exam`);
          loaded.current = true;
          setExam((current) => mergeExam(current, next));
          setLoadError(null);
          setRefreshError(null);
        } catch (cause) {
          const message =
            cause instanceof ApiError && cause.status === 404
              ? "This assessment has no exam yet. Approve the plan and choose Build with AI."
              : errorMessage(cause);
          // A failed refresh keeps the exam on screen and says so.
          if (loaded.current) setRefreshError(`The exam couldn't be refreshed. ${message}`);
          else setLoadError(message);
        }
      } while (reloadAgain.current);
    })().finally(() => {
      reload.current = null;
    });
    reload.current = running;
    return running;
  }, [assessmentId]);

  useEffect(() => {
    void loadExam();
  }, [loadExam]);

  const refresh = useCallback(() => void loadExam(), [loadExam]);

  /** After a tool changed questions: refresh the preview and point at what changed. */
  const showChanges = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      void loadExam().then(() => {
        setHighlighted(new Set(ids));
        if (highlightTimer.current) clearTimeout(highlightTimer.current);
        highlightTimer.current = setTimeout(() => setHighlighted(new Set()), 3000);
        // Bring the changed question into view in the preview, unless it is already.
        setTimeout(
          () => document.getElementById(`question-${ids[0]}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }),
          150,
        );
      });
    },
    [loadExam],
  );

  const finished = useRun(finishRun, (run) => {
    setFinishRun(null);
    if (run.status === "succeeded") router.push(`${base}/exam`);
    else setError(run.errorMessage ?? "Finishing didn't work. Please try again.");
  });

  async function finish() {
    if (!exam || finishing) return;
    setError(null);
    setFinishing(true);
    try {
      const result = await apiRequest<ToolResult>(`/api/exams/${exam.id}/builder/tools/finish_exam`, {
        method: "POST",
        body: { arguments: {} },
        freshSession: true,
      });
      if (result.ok === false) setError(String(result.error ?? "The exam can't be finished yet."));
      else if (result.runId) setFinishRun(result.runId);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setFinishing(false);
    }
  }

  if (loadError && !exam) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Build with AI isn&apos;t available</AlertTitle>
        <AlertDescription>
          {loadError}{" "}
          <Link href={`${base}/plan`} className="underline underline-offset-4">
            Open the plan
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  if (!exam) return <Skeleton className="h-96 w-full rounded-xl" />;
  if (exam.mode !== "interactive") {
    return (
      <Alert>
        <MessagesSquare />
        <AlertTitle>This exam was generated in full</AlertTitle>
        <AlertDescription>
          Open the{" "}
          <Link href={`${base}/exam`} className="underline underline-offset-4">
            exam editor
          </Link>{" "}
          to change its questions with AI.
        </AlertDescription>
      </Alert>
    );
  }
  if (finishRun) {
    return <JobProgress run={finished.run} startingLabel="Finishing the exam…" />;
  }

  const primary = exam.versions[0];
  const questions = exam.questions.filter((question) => question.versionId === primary?.id).sort((a, b) => a.number - b.number);
  const approvedCount = questions.filter((question) => question.status === "approved").length;

  return (
    <div className="flex flex-col gap-4">
      <Tabs value={panel} onValueChange={(value) => setPanel(value as "conversation" | "exam")} className="lg:hidden">
        <TabsList aria-label="Show">
          <TabsTrigger value="conversation">Conversation</TabsTrigger>
          <TabsTrigger value="exam">Exam preview ({questions.length})</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
        {/* Both panels stay mounted on small screens, so a call survives switching tabs. */}
        <BuilderConversation
          examId={exam.id}
          hidden={panel !== "conversation"}
          onChanged={showChanges}
          onFinishRun={setFinishRun}
          onBusyChange={setBusy}
        />

        <section
          aria-label="Exam preview"
          className={cn("flex min-w-0 flex-col gap-4 lg:flex", panel === "exam" ? "flex" : "hidden")}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {questions.length} question{questions.length === 1 ? "" : "s"} · {approvedCount} approved
              {exam.plannedQuestionCount ? ` · about ${exam.plannedQuestionCount} planned` : ""}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <Checkbox id={keyId} checked={showKey} onCheckedChange={(checked) => setShowKey(Boolean(checked))} />
                <Label htmlFor={keyId} className="font-normal">
                  Show answer keys
                </Label>
              </div>
              <Button
                type="button"
                onClick={finish}
                disabled={questions.length === 0 || finishing || busy}
                title={busy ? "Wait until ProfPilot has finished the current change" : undefined}
              >
                {finishing ? <Spinner /> : <CheckCheck />}
                Finish exam
              </Button>
            </div>
          </div>
          {(error || refreshError) && (
            <p role="alert" className="text-sm text-destructive">
              {error ?? refreshError}
            </p>
          )}
          {questions.length === 0 ? (
            <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
              Questions appear here as you create them together.
            </p>
          ) : (
            questions.map((question) => (
              <QuestionCard
                key={question.id}
                question={question}
                showKey={showKey}
                multiVersion={false}
                highlighted={highlighted.has(question.id)}
                onChanged={refresh}
              />
            ))
          )}
          <p className="text-xs text-muted-foreground">
            Finishing creates the other versions of each question and runs the AI quality check. You can keep editing
            afterwards in the{" "}
            <Link href={`${base}/exam`} className={cn(buttonVariants({ variant: "link" }), "h-auto p-0 text-xs")}>
              exam editor
            </Link>
            .
          </p>
        </section>
      </div>
    </div>
  );
}

type BuilderConversationProps = {
  examId: string;
  /** On small screens, while the exam preview tab is shown. */
  hidden: boolean;
  /** Questions a tool changed (the preview refreshes and highlights them). */
  onChanged: (questionIds: string[]) => void;
  onFinishRun: (runId: string) => void;
  onBusyChange: (busy: boolean) => void;
};

/**
 * The conversation with ProfPilot: the voice call, its captions and the text
 * chat. Everything that changes many times a second during a call lives here,
 * so the exam preview next to it doesn't re-render while the professor or
 * ProfPilot is speaking.
 */
const BuilderConversation = memo(function BuilderConversation({
  examId,
  hidden,
  onChanged,
  onFinishRun,
  onBusyChange,
}: BuilderConversationProps) {
  const inputId = useId();
  const [messages, setMessages] = useState<BuilderMessage[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<ConversationStatus | null>(null);
  const voiceSupported = useVoiceSupported();

  useEffect(() => {
    let cancelled = false;
    apiRequest<BuilderMessage[]>(`/api/exams/${examId}/builder/messages`)
      .then((history) => {
        if (!cancelled) setMessages(history);
      })
      // The conversation history is a convenience; the builder works without it.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [examId]);

  /** Shows what ProfPilot is doing (or what failed) in the conversation, right away. */
  const show = useCallback(
    (next: ConversationStatus | null) => {
      setStatus(next);
      onBusyChange(next?.kind === "working");
    },
    [onBusyChange],
  );

  function afterTool(result: ToolResult) {
    onChanged(result.changedQuestionIds ?? []);
    if (result.runId) onFinishRun(result.runId);
  }

  const call = useRealtimeCall({
    getClientSecret: async () => {
      const secret = await apiRequest<RealtimeSecret>("/api/realtime/client-secrets", {
        method: "POST",
        body: { purpose: "builder", examId },
      });
      return secret.clientSecret;
    },
    onToolCall: async (name, args) => {
      // The status appears as the tool starts, before waiting for the AI.
      const label = builderToolStatus(name, args);
      if (label) show({ kind: "working", text: label });
      try {
        const result = await apiRequest<ToolResult>(`/api/exams/${examId}/builder/tools/${encodeURIComponent(name)}`, {
          method: "POST",
          body: { arguments: args },
          freshSession: name === "finish_exam",
        });
        if (result.ok === false) {
          show({ kind: "error", text: `${builderToolFailure(name)} ${result.error ?? ""}`.trim(), onRetry: retryByVoice });
        } else if (label) {
          show(null);
        }
        // The preview refreshes in the background: the call gets the result now.
        afterTool(result);
        return result;
      } catch (cause) {
        show({ kind: "error", text: `${builderToolFailure(name)} ${errorMessage(cause)}`, onRetry: retryByVoice });
        throw cause;
      }
    },
    onTranscript: (entry) => {
      const message: BuilderMessage = {
        id: entry.id,
        role: entry.role,
        channel: "voice",
        content: entry.text,
        createdAt: new Date().toISOString(),
      };
      setMessages((current) => [...current.filter((item) => item.id !== entry.id), message]);
      void apiRequest(`/api/exams/${examId}/builder/transcript`, {
        method: "POST",
        body: { role: entry.role, content: entry.text },
      }).catch(() => undefined);
    },
  });

  function retryByVoice() {
    show(null);
    if (call.state === "connected") call.sendText("Please try that again.");
  }

  /** One chat turn. Its status updates as soon as ProfPilot starts each tool. */
  async function ask(content: string) {
    setSending(true);
    show({ kind: "working", text: "ProfPilot is working on it…" });
    let reply: BuilderChatReply | null = null;
    let failure: string | null = null;
    try {
      await apiStream<BuilderChatEvent>(
        `/api/exams/${examId}/builder/messages`,
        (event) => {
          if (event.event === "tool") {
            const label = builderToolStatus(event.name, event.arguments);
            if (label) show({ kind: "working", text: label });
          } else if (event.event === "done") {
            reply = event;
          } else {
            failure = event.message;
          }
        },
        { method: "POST", body: { content }, freshSession: true },
      );
    } catch (cause) {
      failure = errorMessage(cause);
    } finally {
      setSending(false);
    }
    const answer = reply as BuilderChatReply | null;
    if (failure || !answer) {
      show({ kind: "error", text: failure ?? "ProfPilot didn't reply. Please try again.", onRetry: () => void ask(content) });
      return;
    }
    show(null);
    setMessages((current) => [
      ...current,
      { id: `reply-${Date.now()}`, role: "assistant", channel: "text", content: answer.reply, createdAt: new Date().toISOString() },
    ]);
    afterTool({ changedQuestionIds: answer.changedQuestionIds, runId: answer.runId ?? undefined });
  }

  function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = text.trim();
    if (!content || sending) return;
    setText("");
    if (call.state === "connected") {
      call.sendText(content);
      return;
    }
    setMessages((current) => [
      ...current,
      { id: `local-${Date.now()}`, role: "professor", channel: "text", content, createdAt: new Date().toISOString() },
    ]);
    void ask(content);
  }

  const transcript = call.transcript;
  const entries = useMemo(
    () => [
      ...messages.map((message): ConversationEntry => ({
        id: message.id,
        role: message.role,
        text: message.content,
        channel: message.channel,
      })),
      ...transcript
        .filter((entry) => !entry.final && entry.text.trim())
        .map((entry): ConversationEntry => ({ id: entry.id, role: entry.role, text: entry.text, pending: true })),
    ],
    [messages, transcript],
  );

  return (
    <section
      aria-label="Conversation with ProfPilot"
      className={cn(
        "flex flex-col gap-4 rounded-xl border bg-card p-4 lg:sticky lg:top-[88px] lg:flex lg:max-h-[calc(100svh-7rem)]",
        hidden ? "hidden" : "flex",
      )}
    >
      {voiceSupported ? (
        <VoiceCallControls call={call} onStart={() => void call.start()} />
      ) : (
        <p className="text-sm text-muted-foreground">Voice isn&apos;t supported in this browser. Type below instead.</p>
      )}
      {call.error && (
        <Alert variant={call.microphoneBlocked ? "default" : "destructive"}>
          <AlertDescription>{call.error}</AlertDescription>
        </Alert>
      )}
      <ConversationLog
        entries={entries}
        status={status}
        emptyText="Start a call or type: “Let's start with question 1: a conceptual question on gradient descent.”"
        className="min-h-40 flex-1 max-lg:max-h-[60svh]"
      />
      <form onSubmit={handleSend} className="flex gap-2">
        <label htmlFor={inputId} className="sr-only">
          Message to ProfPilot
        </label>
        <Input
          id={inputId}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={call.state === "connected" ? "Type during the call…" : "Ask for the next question, a change…"}
          autoComplete="off"
          maxLength={4000}
        />
        <Button type="submit" variant="outline" disabled={!text.trim() || sending} aria-label="Send message">
          {sending ? <Spinner /> : <Send />}
        </Button>
      </form>
    </section>
  );
});
