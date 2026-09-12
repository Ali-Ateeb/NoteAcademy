"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/AuthProvider";

export function AccountMenu() {
  const { user, supabase, signOut } = useAuth();
  const router = useRouter();

  // No backend configured, or the initial session check hasn't resolved yet —
  // render nothing rather than flash "Sign in" for an instant before the
  // real state is known.
  if (!supabase || user === undefined) return null;

  if (!user) {
    return (
      <Link href="/login" className="text-sm font-medium text-ink-2 transition-colors hover:text-ink">
        Sign in
      </Link>
    );
  }

  async function onSignOut() {
    await signOut();
    router.push("/");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-3 text-sm">
      <Link href="/dashboard" className="hidden text-ink-2 transition-colors hover:text-ink sm:inline">
        {user.email}
      </Link>
      <button
        type="button"
        onClick={onSignOut}
        className="text-ink-3 transition-colors hover:text-ink"
      >
        Sign out
      </button>
    </div>
  );
}
