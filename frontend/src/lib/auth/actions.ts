"use server";

import type { AuthError } from "@supabase/supabase-js";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

export type AuthFormState = {
  /** A problem with the whole form, such as a wrong password. */
  error?: string;
  /** Problems with single fields, shown under each input. */
  fieldErrors?: { fullName?: string; email?: string; password?: string };
  /** Shown after sign-up when the email address still needs confirming. */
  message?: string;
};

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 8;
const MAX_PASSWORD_LENGTH = 72; // Supabase Auth rejects longer passwords.
const MAX_NAME_LENGTH = 100;

export async function login(
  _state: AuthFormState,
  formData: FormData,
): Promise<AuthFormState> {
  const email = getText(formData, "email");
  const password = getPassword(formData);

  const fieldErrors = {
    email: validateEmail(email),
    password: password ? undefined : "Enter your password.",
  };
  if (hasErrors(fieldErrors)) return { fieldErrors };

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { error: getAuthErrorMessage(error) };

  redirect("/workspace");
}

export async function signup(
  _state: AuthFormState,
  formData: FormData,
): Promise<AuthFormState> {
  const fullName = getText(formData, "fullName");
  const email = getText(formData, "email");
  const password = getPassword(formData);

  const fieldErrors = {
    fullName: validateFullName(fullName),
    email: validateEmail(email),
    password: validateNewPassword(password),
  };
  if (hasErrors(fieldErrors)) return { fieldErrors };

  const supabase = await createClient();
  const origin = (await headers()).get("origin");
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      // Stored with the new user; a database trigger copies it into profiles.full_name.
      data: { full_name: fullName },
      // Where the link in the confirmation email sends the professor.
      emailRedirectTo: origin ? `${origin}/auth/confirm` : undefined,
    },
  });
  if (error) return { error: getAuthErrorMessage(error) };

  // Email confirmation is turned off in Supabase, so the professor is already signed in.
  if (data.session) redirect("/workspace");

  return {
    message: `We sent a confirmation link to ${email}. Open it to activate your account.`,
  };
}

export async function logout() {
  const supabase = await createClient();
  // Ends the session in this browser only; other devices stay signed in.
  await supabase.auth.signOut({ scope: "local" });
  redirect("/login");
}

function getText(formData: FormData, name: string) {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function getPassword(formData: FormData) {
  const value = formData.get("password");
  // Not trimmed: spaces can be part of a password.
  return typeof value === "string" ? value : "";
}

function validateFullName(fullName: string) {
  if (!fullName) return "Enter your full name.";
  if (fullName.length > MAX_NAME_LENGTH) return `Use ${MAX_NAME_LENGTH} characters or fewer.`;
  return undefined;
}

function validateEmail(email: string) {
  if (!email) return "Enter your email address.";
  if (!EMAIL_PATTERN.test(email)) return "Enter a valid email address.";
  return undefined;
}

function validateNewPassword(password: string) {
  if (password.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  if (password.length > MAX_PASSWORD_LENGTH) return `Use ${MAX_PASSWORD_LENGTH} characters or fewer.`;
  return undefined;
}

function hasErrors(fieldErrors: Record<string, string | undefined>) {
  return Object.values(fieldErrors).some(Boolean);
}

/** Turns Supabase Auth errors into friendly messages without exposing internals. */
function getAuthErrorMessage(error: AuthError) {
  switch (error.code) {
    case "invalid_credentials":
      return "Incorrect email or password.";
    case "email_not_confirmed":
      return "Please confirm your email address first. Check your inbox for the link we sent.";
    case "user_already_exists":
    case "email_exists":
      return "An account with this email already exists. Try logging in instead.";
    case "weak_password":
      return "Please choose a stronger password.";
    case "email_address_invalid":
      return "This email address can't be used. Please try another one.";
    case "over_email_send_rate_limit":
    case "over_request_rate_limit":
      return "Too many attempts. Please wait a few minutes and try again.";
    case "signup_disabled":
      return "New sign-ups are currently turned off.";
    default:
      console.error("Unexpected Supabase Auth error:", error);
      return "Something went wrong. Please try again.";
  }
}
