import type { NextRequest } from "next/server";

import { updateSession } from "@/lib/supabase/proxy";

// Next.js runs this before every matching request. (Next.js 16 renamed
// Middleware to Proxy; the file must be called proxy.ts.)
export async function proxy(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  matcher: [
    // Every path except Next.js static files and images.
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
