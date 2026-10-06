// A stand-in for next/navigation whose address tests can change, like a client-side navigation.

import { useSyncExternalStore } from "react";
import { vi } from "vitest";

let pathname = "/workspace/assessments/new";
let searchParams = new URLSearchParams();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export const router = {
  push: vi.fn(),
  replace: vi.fn(),
  prefetch: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
};

export const navigation = {
  useRouter: () => router,
  usePathname: () => useSyncExternalStore(subscribe, () => pathname),
  useSearchParams: () => useSyncExternalStore(subscribe, () => searchParams),
  unstable_rethrow: () => undefined,
  redirect: (url: string) => {
    throw new Error(`redirect to ${url}`);
  },
  notFound: () => {
    throw new Error("not found");
  },
  linkStatus: { pending: false },
};

/** Moves to another address in this app, without reloading (like clicking a <Link>). */
export function navigate(url: string) {
  const next = new URL(url, "http://localhost");
  pathname = next.pathname;
  searchParams = next.searchParams;
  listeners.forEach((listener) => listener());
}

export function resetNavigation() {
  navigation.linkStatus = { pending: false };
  navigate("/workspace/assessments/new");
}
