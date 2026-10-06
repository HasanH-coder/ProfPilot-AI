"use client";

import { Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { RealtimeCall } from "@/lib/realtime/use-realtime-call";
import { cn } from "@/lib/utils";

/** What the call is doing right now, in words (also announced to screen readers). */
export function callStatus(call: RealtimeCall): string {
  switch (call.state) {
    case "requesting_microphone":
      return "Waiting for microphone permission…";
    case "connecting":
      return "Connecting…";
    case "connected":
      if (call.working) return "Working on it…";
      if (call.aiSpeaking) return "ProfPilot is speaking";
      if (call.professorSpeaking) return "Listening to you…";
      return call.muted ? "Microphone muted" : "Listening";
    case "ended":
      return "Call ended";
    case "error":
      return "Call unavailable";
    default:
      return "Not connected";
  }
}

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * The call's duration. It updates itself once a second, so only this small
 * piece re-renders, not the conversation or the page around it.
 */
function CallTimer({ since }: { since: number }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setSeconds(Math.max(0, Math.floor((Date.now() - since) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [since]);
  return <span className="text-sm tabular-nums text-muted-foreground">· {formatElapsed(seconds)}</span>;
}

type VoiceCallControlsProps = {
  call: RealtimeCall;
  onStart: () => void;
  startLabel?: string;
  /** Disables starting (e.g. while the draft is being created). */
  starting?: boolean;
};

/** Start, mute and end a voice call, with its live status and duration. */
export function VoiceCallControls({ call, onStart, startLabel = "Start voice call", starting }: VoiceCallControlsProps) {
  const active = call.state === "connected";
  const busy = starting || call.state === "connecting" || call.state === "requesting_microphone";
  const status = callStatus(call);

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
      {active ? (
        <>
          <Button
            type="button"
            variant="outline"
            aria-pressed={call.muted}
            onClick={() => call.setMuted(!call.muted)}
          >
            {call.muted ? <MicOff /> : <Mic />}
            {call.muted ? "Unmute" : "Mute"}
          </Button>
          <Button type="button" variant="destructive" onClick={call.end}>
            <PhoneOff />
            End call
          </Button>
        </>
      ) : (
        <Button type="button" onClick={onStart} disabled={busy}>
          {busy ? <Spinner /> : <Phone />}
          {busy ? "Connecting…" : call.state === "ended" || call.state === "error" ? "Start a new call" : startLabel}
        </Button>
      )}

      <div className="flex min-w-0 items-center gap-2">
        <p className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground" role="status" aria-live="polite">
          <span
            aria-hidden
            className={cn(
              "size-2 shrink-0 rounded-full",
              active ? "bg-emerald-500" : call.state === "error" ? "bg-destructive" : "bg-muted-foreground/40",
              active && (call.aiSpeaking || call.professorSpeaking) && "motion-safe:animate-pulse",
            )}
          />
          <span>{status}</span>
        </p>
        {/* Outside the live region, so screen readers aren't told the time every second. */}
        {active && call.connectedAt !== null && <CallTimer since={call.connectedAt} />}
      </div>
    </div>
  );
}
