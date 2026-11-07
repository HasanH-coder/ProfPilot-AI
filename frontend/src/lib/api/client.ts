// Calls the ProfPilot AI API (FastAPI) from the browser.
//
// Every request carries the professor's Supabase access token, which the API
// verifies; the API then works as that professor, so Row Level Security
// applies. No AI key ever reaches the browser: the API holds it.

import { createClient } from "@/lib/supabase/client";

/** Where the AI API runs. Public: it's only an address. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** An error the API (or the network) reported, with a message safe to show. */
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const UNREACHABLE =
  "ProfPilot's AI service can't be reached right now. Check your connection, or try again in a moment.";
// Refresh the token when it has less than this left, so long requests don't outlive it.
const REFRESH_MARGIN_MS = 5 * 60 * 1000;

async function accessToken(fresh: boolean): Promise<string> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  let session = data.session;
  const expiresSoon = session?.expires_at ? session.expires_at * 1000 - Date.now() < REFRESH_MARGIN_MS : true;
  if (session && (fresh || expiresSoon)) {
    const refreshed = await supabase.auth.refreshSession();
    session = refreshed.data.session ?? session;
  }
  if (!session) throw new ApiError("not_authenticated", "Your session has ended. Please log in again.", 401);
  return session.access_token;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Start long jobs with a fresh token, so it stays valid while they run. */
  freshSession?: boolean;
};

async function send(
  path: string,
  { method = "GET", body, signal, freshSession = false }: RequestOptions,
  accept?: string,
) {
  const token = await accessToken(freshSession);
  try {
    return await fetch(`${API_URL}${path}`, {
      method,
      signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(accept ? { Accept: accept } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("network_error", UNREACHABLE, 0);
  }
}

async function errorFrom(response: Response): Promise<ApiError> {
  const data = await response.json().catch(() => null);
  const error = data?.error;
  return new ApiError(
    typeof error?.code === "string" ? error.code : "error",
    typeof error?.message === "string" ? error.message : "Something went wrong. Please try again.",
    response.status,
  );
}

/** A JSON request. Throws ApiError with a readable message when it fails. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * A request whose reply streams as JSON lines (application/x-ndjson): each
 * line is passed to `onEvent` as soon as it arrives. A plain JSON reply is
 * passed on as a single `done` event.
 */
export async function apiStream<T extends { event: string }>(
  path: string,
  onEvent: (event: T) => void,
  options: RequestOptions = {},
): Promise<void> {
  const response = await send(path, options, "application/x-ndjson");
  if (!response.ok) throw await errorFrom(response);
  if (!response.body || !response.headers.get("Content-Type")?.includes("ndjson")) {
    onEvent({ event: "done", ...(await response.json()) } as T);
    return;
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffered = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffered += value ?? "";
      const lines = buffered.split("\n");
      buffered = done ? "" : (lines.pop() ?? "");
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as T);
      if (done) return;
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof SyntaxError) throw new ApiError("error", "ProfPilot's reply couldn't be read. Please try again.", 0);
    throw new ApiError("network_error", UNREACHABLE, 0);
  }
}

type LongEvent = { event: string; code?: string; message?: string; [key: string]: unknown };

/**
 * A request that can take a minute or more (an AI edit). The API streams blank
 * keepalive lines while it works, so the hosting gateway doesn't drop the
 * connection as idle, then sends the reply. Throws ApiError like apiRequest.
 */
export async function apiRequestLong<T>(path: string, options: RequestOptions = {}): Promise<T> {
  let result: T | undefined;
  let failure: ApiError | undefined;
  await apiStream<LongEvent>(
    path,
    (event) => {
      // Recorded, not thrown: apiStream would report a throw from here as a lost connection.
      if (event.event === "done") {
        result = Object.fromEntries(Object.entries(event).filter(([key]) => key !== "event")) as T;
      } else if (event.event === "error") {
        failure = new ApiError(event.code ?? "error", event.message ?? "Something went wrong. Please try again.", 500);
      }
    },
    options,
  );
  if (failure) throw failure;
  if (result === undefined) throw new ApiError("network_error", UNREACHABLE, 0);
  return result;
}

/** Downloads a file (e.g. an exam PDF) and saves it with the name the API gives it. */
export async function apiDownload(path: string): Promise<void> {
  const response = await send(path, {});
  if (!response.ok) throw await errorFrom(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "exam.pdf";
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a moment to start the download before freeing the file.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The message to show for any error. */
export function errorMessage(error: unknown, fallback = "Something went wrong. Please try again."): string {
  if (error instanceof ApiError) return error.message;
  return fallback;
}
