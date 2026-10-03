import type { EmailOtpType } from "@supabase/supabase-js";
import { redirect } from "next/navigation";
import type { NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * The link in the sign-up confirmation email lands here. Supabase sends either
 * a `token_hash` (custom email template, works in any browser) or a `code`
 * (default template, works in the browser used to sign up). On success the
 * professor is signed in and taken to their workspace.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const tokenHash = params.get("token_hash");
  const type = params.get("type") as EmailOtpType | null;
  const code = params.get("code");

  if (tokenHash && type) {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
    if (!error) redirect("/workspace");
  } else if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) redirect("/workspace");
  }

  redirect("/login?confirmation=failed");
}
