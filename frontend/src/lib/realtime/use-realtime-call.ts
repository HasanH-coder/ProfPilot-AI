"use client";

// A live voice conversation with ProfPilot over WebRTC (OpenAI Realtime API).
//
// The browser never holds the permanent API key: it asks ProfPilot's API for a
// short-lived client secret, then connects directly to OpenAI with it. When the
// model wants to change something (the form, the exam), it calls a tool; this
// hook hands the call to `onToolCall`, which runs it through ProfPilot's API
// (with the professor's own permissions) and sends the result back.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

const REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

export type CallState = "idle" | "requesting_microphone" | "connecting" | "connected" | "ended" | "error";

export type TranscriptEntry = {
  id: string;
  role: "professor" | "assistant";
  text: string;
  /** False while the words are still arriving. */
  final: boolean;
};

type Options = {
  /** Gets a fresh client secret from ProfPilot's API. */
  getClientSecret: () => Promise<string>;
  /** Runs a tool the model chose and returns its result for the model. */
  onToolCall: (name: string, args: Record<string, unknown>) => Promise<unknown>;
  /** Called with every finished line of the conversation (e.g. to keep a history). */
  onTranscript?: (entry: TranscriptEntry) => void;
};

type ServerEvent = { type: string; [key: string]: unknown };
type FunctionCallItem = { type: "function_call"; name: string; call_id: string; arguments: string };

export function isVoiceSupported() {
  return (
    typeof window !== "undefined" &&
    typeof window.RTCPeerConnection !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

function subscribeNever() {
  return () => {};
}

/** Whether this browser can make voice calls. Assumes yes while rendering on the server. */
export function useVoiceSupported() {
  return useSyncExternalStore(subscribeNever, isVoiceSupported, () => true);
}

export function useRealtimeCall({ getClientSecret, onToolCall, onTranscript }: Options) {
  const [state, setState] = useState<CallState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [microphoneBlocked, setMicrophoneBlocked] = useState(false);
  const [muted, setMutedState] = useState(false);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  const [professorSpeaking, setProfessorSpeaking] = useState(false);
  const [working, setWorking] = useState(false);
  const [captionsUnavailable, setCaptionsUnavailable] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [elapsed, setElapsed] = useState(0);

  const peer = useRef<RTCPeerConnection | null>(null);
  const channel = useRef<RTCDataChannel | null>(null);
  const microphone = useRef<MediaStream | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  // Latest callbacks, so a long call always uses the current form or exam.
  const callbacks = useRef({ onToolCall, onTranscript, getClientSecret });
  useEffect(() => {
    callbacks.current = { onToolCall, onTranscript, getClientSecret };
  });

  const upsert = useCallback((entry: TranscriptEntry) => {
    setTranscript((current) => {
      const index = current.findIndex((item) => item.id === entry.id);
      if (index === -1) return [...current, entry];
      const next = [...current];
      next[index] = entry;
      return next;
    });
    if (entry.final && entry.text.trim()) callbacks.current.onTranscript?.(entry);
  }, []);

  const send = useCallback((event: Record<string, unknown>) => {
    if (channel.current?.readyState === "open") channel.current.send(JSON.stringify(event));
  }, []);

  const cleanUp = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    channel.current?.close();
    channel.current = null;
    microphone.current?.getTracks().forEach((track) => track.stop());
    microphone.current = null;
    peer.current?.close();
    peer.current = null;
    if (audio.current) audio.current.srcObject = null;
    setAiSpeaking(false);
    setProfessorSpeaking(false);
    setWorking(false);
  }, []);

  const end = useCallback(() => {
    cleanUp();
    setState((current) => (current === "idle" ? current : "ended"));
  }, [cleanUp]);

  // End the call if the page goes away.
  useEffect(() => () => cleanUp(), [cleanUp]);

  const runToolCalls = useCallback(
    async (calls: FunctionCallItem[]) => {
      setWorking(true);
      try {
        for (const call of calls) {
          let output: unknown;
          try {
            const args = call.arguments ? (JSON.parse(call.arguments) as Record<string, unknown>) : {};
            output = await callbacks.current.onToolCall(call.name, args);
          } catch (cause) {
            output = { ok: false, error: cause instanceof Error ? cause.message : "The change couldn't be made." };
          }
          send({
            type: "conversation.item.create",
            item: { type: "function_call_output", call_id: call.call_id, output: JSON.stringify(output) },
          });
        }
        // One response after all results, so the model answers once.
        send({ type: "response.create" });
      } finally {
        setWorking(false);
      }
    },
    [send],
  );

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case "input_audio_buffer.speech_started":
          setProfessorSpeaking(true);
          break;
        case "input_audio_buffer.speech_stopped":
          setProfessorSpeaking(false);
          break;
        case "output_audio_buffer.started":
          setAiSpeaking(true);
          break;
        case "output_audio_buffer.stopped":
        case "output_audio_buffer.cleared":
          setAiSpeaking(false);
          break;
        case "conversation.item.input_audio_transcription.delta":
          upsertDelta(event, "professor");
          break;
        case "conversation.item.input_audio_transcription.completed":
          upsert({ id: String(event.item_id), role: "professor", text: String(event.transcript ?? ""), final: true });
          break;
        case "conversation.item.input_audio_transcription.failed":
          setCaptionsUnavailable(true);
          break;
        case "response.output_audio_transcript.delta":
        case "response.output_text.delta":
          upsertDelta(event, "assistant");
          break;
        case "response.output_audio_transcript.done":
          upsert({ id: String(event.item_id), role: "assistant", text: String(event.transcript ?? ""), final: true });
          break;
        case "response.output_text.done":
          upsert({ id: String(event.item_id), role: "assistant", text: String(event.text ?? ""), final: true });
          break;
        case "response.done": {
          const response = event.response as { output?: { type: string }[] } | undefined;
          const calls = (response?.output ?? []).filter((item): item is FunctionCallItem => item.type === "function_call");
          if (calls.length > 0) void runToolCalls(calls);
          break;
        }
        case "error": {
          const detail = event.error as { message?: string; code?: string } | undefined;
          // Most errors are about one event and the call continues; show the message briefly.
          setError(detail?.message ? "ProfPilot had a problem with the last request. You can keep talking." : null);
          break;
        }
      }

      function upsertDelta(delta: ServerEvent, role: TranscriptEntry["role"]) {
        const id = String(delta.item_id);
        const piece = String(delta.delta ?? "");
        setTranscript((current) => {
          const index = current.findIndex((item) => item.id === id);
          if (index === -1) return [...current, { id, role, text: piece, final: false }];
          if (current[index].final) return current;
          const next = [...current];
          next[index] = { ...next[index], text: next[index].text + piece };
          return next;
        });
      }
    },
    [runToolCalls, upsert],
  );

  const start = useCallback(async () => {
    if (state === "connecting" || state === "connected" || state === "requesting_microphone") return;
    setError(null);
    setMicrophoneBlocked(false);
    setCaptionsUnavailable(false);
    setTranscript([]);
    setElapsed(0);
    setMutedState(false);

    if (!isVoiceSupported()) {
      setState("error");
      setError("This browser doesn't support voice calls. You can type instead.");
      return;
    }

    setState("requesting_microphone");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (cause) {
      const name = cause instanceof DOMException ? cause.name : "";
      setState("error");
      if (name === "NotAllowedError" || name === "SecurityError") {
        setMicrophoneBlocked(true);
        setError("Microphone access is blocked. Allow it in your browser's site settings, or type instead.");
      } else if (name === "NotFoundError") {
        setError("No microphone was found. Connect one, or type instead.");
      } else {
        setError("The microphone couldn't be started. It may be in use by another app. You can type instead.");
      }
      return;
    }
    microphone.current = stream;

    setState("connecting");
    try {
      const secret = await callbacks.current.getClientSecret();
      const connection = new RTCPeerConnection();
      peer.current = connection;

      // A fresh audio element for each call plays ProfPilot's voice.
      const player = new Audio();
      player.autoplay = true;
      audio.current = player;
      connection.ontrack = (event) => {
        player.srcObject = event.streams[0];
      };
      stream.getAudioTracks().forEach((track) => connection.addTrack(track, stream));

      const events = connection.createDataChannel("oai-events");
      channel.current = events;
      events.onmessage = (message) => {
        try {
          handleEvent(JSON.parse(String(message.data)) as ServerEvent);
        } catch {
          // Ignore malformed events.
        }
      };
      events.onopen = () => {
        setState("connected");
        timer.current = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
        // Let ProfPilot open the conversation.
        send({ type: "response.create" });
      };
      connection.onconnectionstatechange = () => {
        if (connection.connectionState === "failed" || connection.connectionState === "disconnected") {
          cleanUp();
          setState("error");
          setError("The call was disconnected. Start it again, or type instead.");
        }
      };

      const offer = await connection.createOffer();
      await connection.setLocalDescription(offer);
      const response = await fetch(REALTIME_CALLS_URL, {
        method: "POST",
        body: offer.sdp,
        headers: { Authorization: `Bearer ${secret}`, "Content-Type": "application/sdp" },
      });
      if (!response.ok) throw new Error("The voice service refused the connection.");
      await connection.setRemoteDescription({ type: "answer", sdp: await response.text() });
    } catch (cause) {
      cleanUp();
      setState("error");
      setError(
        cause instanceof Error && cause.message && !cause.message.startsWith("Failed to fetch")
          ? `${cause.message} You can type instead.`
          : "The voice call couldn't be connected. Check your connection, or type instead.",
      );
    }
  }, [cleanUp, handleEvent, send, state]);

  const setMuted = useCallback((next: boolean) => {
    microphone.current?.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
    setMutedState(next);
  }, []);

  /** Types a message into the live call (the model sees it like speech). */
  const sendText = useCallback(
    (text: string) => {
      const id = `typed-${Date.now()}`;
      send({
        type: "conversation.item.create",
        item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
      });
      send({ type: "response.create" });
      upsert({ id, role: "professor", text, final: true });
    },
    [send, upsert],
  );

  return {
    state,
    error,
    microphoneBlocked,
    muted,
    setMuted,
    aiSpeaking,
    professorSpeaking,
    working,
    captionsUnavailable,
    transcript,
    elapsed,
    start,
    end,
    sendText,
  };
}

export type RealtimeCall = ReturnType<typeof useRealtimeCall>;
