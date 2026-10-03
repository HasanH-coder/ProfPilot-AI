import "server-only";

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { getSupabaseConfig } from "@/lib/supabase/config";
import type { Database } from "@/lib/supabase/database.types";

/**
 * Supabase client for Server Components, Server Actions and Route Handlers.
 * It reads the professor's session from cookies. Create a new client for every
 * request; never share one between requests.
 */
export async function createClient() {
  // Reading cookies first also marks the calling page as per-request, so Next.js
  // never pre-renders signed-in pages at build time.
  const cookieStore = await cookies();
  const { url, publishableKey } = getSupabaseConfig();

  return createServerClient<Database>(url, publishableKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // Server Components can't set cookies. That's fine: src/proxy.ts
          // refreshes the session before any page renders.
        }
      },
    },
  });
}
