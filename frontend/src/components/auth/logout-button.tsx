"use client";

import { LogOut } from "lucide-react";
import { useFormStatus } from "react-dom";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { logout } from "@/lib/auth/actions";

export function LogoutButton() {
  return (
    <form action={logout}>
      <SubmitButton />
    </form>
  );
}

// useFormStatus only works in a component rendered inside the <form>.
function SubmitButton() {
  const { pending } = useFormStatus();

  return (
    <Button type="submit" variant="ghost" size="sm" disabled={pending}>
      {pending ? <Spinner /> : <LogOut />}
      Log out
    </Button>
  );
}
