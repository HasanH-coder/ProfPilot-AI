// A fake voice call: WebRTC, the microphone and OpenAI's call endpoint are
// replaced, and the test sends the events the Realtime API would.

import { vi } from "vitest";

class FakeChannel {
  readyState: RTCDataChannelState = "connecting";
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = "closed";
  }
}

export function installFakeRealtime() {
  const channels: FakeChannel[] = [];

  class FakePeerConnection {
    connectionState = "new";
    ontrack: unknown = null;
    onconnectionstatechange: unknown = null;
    private channel: FakeChannel | null = null;
    addTrack() {}
    createDataChannel() {
      this.channel = new FakeChannel();
      channels.push(this.channel);
      return this.channel;
    }
    async createOffer() {
      return { type: "offer", sdp: "v=0" };
    }
    async setLocalDescription() {}
    async setRemoteDescription() {
      setTimeout(() => {
        if (!this.channel) return;
        this.channel.readyState = "open";
        this.channel.onopen?.();
      }, 0);
    }
    close() {}
  }

  vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
  vi.spyOn(navigator.mediaDevices, "getUserMedia").mockImplementation(async () => new MediaStream());
  const realFetch = globalThis.fetch;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
    String(input).startsWith("https://api.openai.com/")
      ? Promise.resolve(new Response("v=0", { status: 201, headers: { "Content-Type": "application/sdp" } }))
      : realFetch(input, init),
  );

  return {
    /** Sends a server event, as OpenAI Realtime does during a call. */
    emit(event: Record<string, unknown>) {
      channels.at(-1)?.onmessage?.({ data: JSON.stringify(event) });
    },
    /** What the page sent to the model. */
    sent() {
      return channels.at(-1)?.sent ?? [];
    },
  };
}

/** Waits for the next turn of the event loop, as separate network messages would. */
export const nextTask = () => new Promise((resolve) => setTimeout(resolve, 2));

/**
 * A busy stretch of conversation: many caption pieces and speaking events,
 * each arriving on its own, like real messages from the call.
 */
export async function emitBusyConversation(emit: (event: Record<string, unknown>) => void, pieces = 200) {
  for (let index = 0; index < pieces; index++) {
    const turn = Math.floor(index / 50);
    const professor = turn % 2 === 0;
    if (index % 50 === 0) {
      emit({ type: professor ? "input_audio_buffer.speech_started" : "output_audio_buffer.started" });
    }
    emit({
      type: professor ? "conversation.item.input_audio_transcription.delta" : "response.output_audio_transcript.delta",
      item_id: `item-${turn}`,
      delta: "word ",
    });
    if (index % 50 === 49) {
      emit({ type: professor ? "input_audio_buffer.speech_stopped" : "output_audio_buffer.stopped" });
    }
    await nextTask();
  }
}
