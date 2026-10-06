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

async function send(path: string, { method = "GET", body, signal, freshSession = false }: RequestOptions) {
  const token = await accessToken(freshSession);
  try {
    return await fetch(`${API_URL}${path}`, {
      method,
      signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
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
