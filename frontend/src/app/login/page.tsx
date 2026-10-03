import type { Metadata } from "next";

import { AuthShell } from "@/components/auth/auth-shell";
import { LoginForm } from "@/components/auth/login-form";

export const metadata: Metadata = { title: "Log in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  // Set by /auth/confirm when an email confirmation link could not be used.
  const { confirmation } = await searchParams;
  const notice =
    confirmation === "failed"
      ? "That confirmation link didn't work. If you've already confirmed your email, log in below. Otherwise, sign up again to get a new link."
      : undefined;

  return (
    <AuthShell>
      <LoginForm notice={notice} />
    </AuthShell>
  );
}
