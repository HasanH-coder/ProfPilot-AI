"use client";

import { useLinkStatus } from "next/link";
import type { ReactNode } from "react";

import { Spinner } from "@/components/ui/spinner";

/**
 * A link's icon that turns into a spinner while the linked page loads, so a
 * click always gets a visible response. Must be placed inside a <Link>.
 */
export function LinkPendingIcon({ children }: { children: ReactNode }) {
  const { pending } = useLinkStatus();
  return pending ? <Spinner aria-hidden /> : children;
}
