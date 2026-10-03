import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { getSupabaseConfig, isSupabaseConfigured } from "@/lib/supabase/config";
import type { Database } from "@/lib/supabase/database.types";

const AUTH_PAGES = ["/login", "/signup"];

/**
 * Runs before every page request (see src/proxy.ts) to:
 * 1. refresh the professor's session cookie when it is about to expire, and
 * 2. send signed-out visitors from /workspace to /login, and signed-in
 *    professors from /login and /signup to /workspace.
 *
 * These redirects are a convenience only. The real protection is on the server
 * (lib/auth/current-professor.ts) and in the database (Row Level Security).
 */
export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });

  // Supabase isn't set up yet (see frontend/.env.example), so public pages keep working.
  if (!isSupabaseConfigured) return response;

  const { url, publishableKey } = getSupabaseConfig();
  const supabase = createServerClient<Database>(url, publishableKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        // Give the refreshed session to the page that is about to render...
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        // ...and send it back to the browser, with headers that stop CDNs caching it.
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
        Object.entries(headers).forEach(([key, value]) => response.headers.set(key, value));
      },
    },
  });

  // Verifies the session and refreshes it when needed. Keep this call directly
  // after creating the client, otherwise professors may be signed out at random.
  const { data } = await supabase.auth.getClaims();
  const isSignedIn = Boolean(data?.claims);

  const { pathname } = request.nextUrl;
  const isWorkspace = pathname === "/workspace" || pathname.startsWith("/workspace/");

  if (!isSignedIn && isWorkspace) {
    return redirectTo("/login", request, response);
  }
  if (isSignedIn && AUTH_PAGES.includes(pathname)) {
    return redirectTo("/workspace", request, response);
  }

  return response;
}

/** Redirects while keeping any session cookies refreshed above. */
function redirectTo(pathname: string, request: NextRequest, response: NextResponse) {
  const redirect = NextResponse.redirect(new URL(pathname, request.url));
  response.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
  // Where you are sent depends on who is signed in, so never cache it.
  redirect.headers.set("Cache-Control", "private, no-store");
  return redirect;
}
