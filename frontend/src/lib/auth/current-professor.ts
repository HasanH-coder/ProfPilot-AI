import "server-only";

import { redirect } from "next/navigation";
import { cache } from "react";

import { createClient } from "@/lib/supabase/server";

/** The signed-in professor, as shown in the workspace. */
export type Professor = {
  id: string;
  email: string;
  fullName: string | null;
  institution: string | null;
};

/**
 * Returns the signed-in professor and their profile, or redirects to /login.
 * Call it in every workspace page: a check in the layout alone is not enough.
 * Wrapped in `cache`, so several calls during one request only query once.
 */
export const getCurrentProfessor = cache(async (): Promise<Professor> => {
  const supabase = await createClient();

  // Verifies the session token. Never trust getSession() on the server.
  const { data } = await supabase.auth.getClaims();
  const claims = data?.claims;
  if (!claims) redirect("/login");

  // Row Level Security guarantees this can only return the professor's own profile.
  const { data: profile, error } = await supabase
    .from("profiles")
    .select("full_name, institution")
    .eq("id", claims.sub)
    .maybeSingle();
  if (error) throw new Error(`Could not load the professor's profile: ${error.message}`);

  return {
    id: claims.sub,
    email: claims.email ?? "",
    fullName: profile?.full_name ?? null,
    institution: profile?.institution ?? null,
  };
});
