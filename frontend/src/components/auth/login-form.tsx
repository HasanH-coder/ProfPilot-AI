"use client";

import Link from "next/link";
import { useActionState, useState } from "react";

import { FormField } from "@/components/form-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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
import { login, type AuthFormState } from "@/lib/auth/actions";

const initialState: AuthFormState = {};

/** `notice` is an optional message from the page, e.g. after a failed email confirmation. */
export function LoginForm({ notice }: { notice?: string }) {
  const [state, formAction, isPending] = useActionState(login, initialState);
  // Controlled, so it survives the form reset React does after each submit.
  // The password field is left uncontrolled on purpose, so it is cleared.
  const [email, setEmail] = useState("");

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-xl">
          <h1>Welcome back</h1>
        </CardTitle>
        <CardDescription>Log in to your ProfPilot AI workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        <form action={formAction} noValidate>
          <FieldGroup>
            {state.error ? (
              <Alert variant="destructive">
                <AlertDescription>{state.error}</AlertDescription>
              </Alert>
            ) : (
              notice && (
                <Alert>
                  <AlertDescription>{notice}</AlertDescription>
                </Alert>
              )
            )}
            <FormField
              id="email"
              name="email"
              label="Email"
              type="email"
              autoComplete="email"
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
              autoComplete="current-password"
              required
              error={state.fieldErrors?.password}
            />
            <Button type="submit" size="lg" disabled={isPending}>
              {isPending && <Spinner />}
              Log in
            </Button>
          </FieldGroup>
        </form>
      </CardContent>
      <CardFooter className="justify-center gap-1 text-muted-foreground">
        Don&apos;t have an account?
        <Link href="/signup" className="font-medium text-foreground underline-offset-4 hover:underline">
          Sign up
        </Link>
      </CardFooter>
    </Card>
  );
}
