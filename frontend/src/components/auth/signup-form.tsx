"use client";

import { ArrowRight, MailCheck } from "lucide-react";
import Link from "next/link";
import { useActionState, useState } from "react";

import { FormField } from "@/components/form-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FieldGroup } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { signup, type AuthFormState } from "@/lib/auth/actions";
import { cn } from "@/lib/utils";

const initialState: AuthFormState = {};

export function SignupForm() {
  const [state, formAction, isPending] = useActionState(signup, initialState);
  // Controlled, so they survive the form reset React does after each submit.
  // The password field is left uncontrolled on purpose, so it is cleared.
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");

  // Supabase is waiting for the professor to confirm their email address.
  if (state.message) {
    return (
      <Card role="status" className="auth-form-card">
        <CardHeader>
          <span className="public-icon mb-3">
            <MailCheck className="size-5" />
          </span>
          <CardTitle className="auth-form-title">
            <h1>Check your email</h1>
          </CardTitle>
          <CardDescription>{state.message}</CardDescription>
        </CardHeader>
        <CardFooter>
          <Link href="/login" className={cn(buttonVariants({ variant: "outline" }), "w-full")}>
            Back to log in
          </Link>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card className="auth-form-card">
      <CardHeader>
        <p className="public-eyebrow">GET STARTED</p>
        <CardTitle className="auth-form-title">
          <h1>Create your account</h1>
        </CardTitle>
        <CardDescription>Set up your ProfPilot AI workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        <form action={formAction} noValidate>
          <FieldGroup>
            {state.error && (
              <Alert variant="destructive">
                <AlertDescription>{state.error}</AlertDescription>
              </Alert>
            )}
            <FormField
              id="fullName"
              name="fullName"
              label="Full name"
              autoComplete="name"
              placeholder="Your full name"
              required
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              error={state.fieldErrors?.fullName}
            />
            <FormField
              id="email"
              name="email"
              label="Email"
              type="email"
              autoComplete="email"
              placeholder="you@university.edu"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              error={state.fieldErrors?.email}
            />
            <FormField
              id="password"
              name="password"
              label="Password"
              type="password"
              autoComplete="new-password"
              required
              description="At least 8 characters."
              error={state.fieldErrors?.password}
            />
            <Button type="submit" size="lg" disabled={isPending}>
              {isPending && <Spinner />}
              Create account
              {!isPending && <ArrowRight data-icon="inline-end" aria-hidden="true" />}
            </Button>
          </FieldGroup>
        </form>
      </CardContent>
      <CardFooter className="justify-center gap-1 text-muted-foreground">
        Already have an account?
        <Link href="/login" className="font-medium text-primary underline-offset-4 hover:underline">
          Log in
        </Link>
      </CardFooter>
    </Card>
  );
}
