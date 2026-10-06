"use client";

import { CheckCheck, MessagesSquare, Send } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { ConversationLog, type ConversationEntry } from "@/components/ai/conversation-log";
import { JobProgress } from "@/components/ai/job-progress";
import { VoiceCallControls } from "@/components/ai/voice-call-controls";
import { QuestionCard } from "@/components/exam/question-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type { BuilderChatReply, BuilderMessage, Exam, RealtimeSecret, ToolResult } from "@/lib/api/types";
import { useRealtimeCall, useVoiceSupported } from "@/lib/realtime/use-realtime-call";
import { cn } from "@/lib/utils";

/**
 * "Build with AI": the professor and ProfPilot write the exam question by
 * question, by voice or text, while the exam preview updates live.
 */
export function ExamBuilder({ assessmentId }: { assessmentId: string }) {
  const router = useRouter();
  const inputId = useId();
  const keyId = useId();
  const [exam, setExam] = useState<Exam | null>(null);
  const [messages, setMessages] = useState<BuilderMessage[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [finishRun, setFinishRun] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<Set<string>>(() => new Set());
  const [panel, setPanel] = useState<"conversation" | "exam">("conversation");
  const [showKey, setShowKey] = useState(false);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const voiceSupported = useVoiceSupported();
  const examId = exam?.id ?? null;
  const base = `/workspace/assessments/${assessmentId}`;

  const loadExam = useCallback(async () => {
    try {
      const next = await apiRequest<Exam>(`/api/assessments/${assessmentId}/exam`);
      setExam(next);
      setLoadError(null);
      return next;
    } catch (cause) {
      setLoadError(
        cause instanceof ApiError && cause.status === 404
          ? "This assessment has no exam yet. Approve the plan and choose Build with AI."
          : errorMessage(cause),
      );
      return null;
    }
  }, [assessmentId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const loaded = await loadExam();
      if (!loaded || cancelled) return;
      try {
        const history = await apiRequest<BuilderMessage[]>(`/api/exams/${loaded.id}/builder/messages`);
        if (!cancelled) setMessages(history);
      } catch {
        // The conversation history is a convenience; the builder works without it.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadExam]);

  function highlight(ids: string[]) {
    if (ids.length === 0) return;
    setHighlighted(new Set(ids));
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlighted(new Set()), 3000);
    // Bring the changed question into view in the preview.
    setTimeout(() => document.getElementById(`question-${ids[0]}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 150);
  }

  async function afterTool(result: ToolResult) {
    const changed = result.changedQuestionIds ?? [];
    if (changed.length > 0) {
      await loadExam();
      highlight(changed);
    }
    if (result.runId) setFinishRun(result.runId);
  }

  const call = useRealtimeCall({
    getClientSecret: async () => {
      if (!examId) throw new Error("The exam isn't ready yet.");
      const secret = await apiRequest<RealtimeSecret>("/api/realtime/client-secrets", {
        method: "POST",
        body: { purpose: "builder", examId },
      });
      return secret.clientSecret;
    },
    onToolCall: async (name, args) => {
      if (!examId) return { ok: false, error: "The exam isn't ready yet." };
      const result = await apiRequest<ToolResult>(`/api/exams/${examId}/builder/tools/${encodeURIComponent(name)}`, {
        method: "POST",
        body: { arguments: args },
        freshSession: name === "finish_exam",
      });
      await afterTool(result);
      return result;
    },
    onTranscript: (entry) => {
      if (!examId) return;
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

  const finishing = useRun(finishRun, (finished) => {
    setFinishRun(null);
    if (finished.status === "succeeded") router.push(`${base}/exam`);
    else setError(finished.errorMessage ?? "Finishing didn't work. Please try again.");
  });

  async function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = text.trim();
    if (!content || sending || !examId) return;
    setText("");
    setError(null);
    if (call.state === "connected") {
      call.sendText(content);
      return;
    }
    const optimistic: BuilderMessage = {
      id: `local-${Date.now()}`,
      role: "professor",
      channel: "text",
      content,
      createdAt: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);
    setSending(true);
    try {
      const reply = await apiRequest<BuilderChatReply>(`/api/exams/${examId}/builder/messages`, {
        method: "POST",
        body: { content },
        freshSession: true,
      });
      setMessages((current) => [
        ...current,
        { id: `reply-${Date.now()}`, role: "assistant", channel: "text", content: reply.reply, createdAt: new Date().toISOString() },
      ]);
      await afterTool({ changedQuestionIds: reply.changedQuestionIds, runId: reply.runId ?? undefined });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSending(false);
    }
  }

  async function finish() {
    if (!examId) return;
    setError(null);
    try {
      const result = await apiRequest<ToolResult>(`/api/exams/${examId}/builder/tools/finish_exam`, {
        method: "POST",
        body: { arguments: {} },
        freshSession: true,
      });
      if (result.ok === false) setError(String(result.error ?? "The exam can't be finished yet."));
      else if (result.runId) setFinishRun(result.runId);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  if (loadError) {
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
    return <JobProgress run={finishing.run} startingLabel="Finishing the exam…" />;
  }

  const primary = exam.versions[0];
  const questions = exam.questions.filter((question) => question.versionId === primary?.id).sort((a, b) => a.number - b.number);
  const approvedCount = questions.filter((question) => question.status === "approved").length;
  const entries: ConversationEntry[] = [
    ...messages.map((message): ConversationEntry => ({
      id: message.id,
      role: message.role,
      text: message.content,
      channel: message.channel,
    })),
    ...call.transcript
      .filter((entry) => !entry.final && entry.text.trim())
      .map((entry): ConversationEntry => ({ id: entry.id, role: entry.role, text: entry.text, pending: true })),
  ];

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
        <section
          aria-label="Conversation with ProfPilot"
          className={cn(
            "flex flex-col gap-4 rounded-xl border bg-card p-4 lg:sticky lg:top-10 lg:flex lg:max-h-[calc(100svh-5rem)]",
            panel === "conversation" ? "flex" : "hidden",
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
            emptyText="Start a call or type: “Let's start with question 1: a conceptual question on gradient descent.”"
            className="min-h-40 flex-1"
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
          {sending && (
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="size-3.5" aria-hidden />
              ProfPilot is working on it…
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </section>

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
              <Button type="button" onClick={finish} disabled={questions.length === 0}>
                <CheckCheck />
                Finish exam
              </Button>
            </div>
          </div>
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
                onChanged={() => void loadExam()}
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
