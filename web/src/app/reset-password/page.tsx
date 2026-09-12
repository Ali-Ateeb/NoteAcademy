"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";

/**
 * Landed on only after `/auth/callback` has already exchanged the reset
 * link's code for a live (recovery-scoped) session — `updateUser` below acts
 * on whichever session is current, which is what makes this page correct
 * without it needing to know anything about *whose* password it is
 * resetting.
 */
export default function ResetPasswordPage() {
  const { supabase, user } = useAuth();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabase) return;
    setSubmitting(true);
    setError(null);

    const { error: updateError } = await supabase.auth.updateUser({ password });

    setSubmitting(false);
    if (updateError) {
      setError(updateError.message);
      return;
    }
    setDone(true);
    setTimeout(() => {
      router.push("/dashboard");
      router.refresh();
    }, 1500);
  }

  if (done) {
    return (
      <div className="mx-auto max-w-sm px-5 py-16 text-center">
        <h1 className="font-serif text-3xl tracking-tight text-ink">Password updated</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-2">Taking you to your dashboard…</p>
      </div>
    );
  }

  if (user === undefined) {
    return <div className="py-24 text-center text-ink-3">Loading…</div>;
  }

  if (!user) {
    return (
      <div className="mx-auto max-w-sm px-5 py-16 text-center">
        <h1 className="font-serif text-3xl tracking-tight text-ink">Link expired</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-2">
          This password reset link is no longer valid. Request a new one from the
          sign-in page.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm px-5 py-16">
      <h1 className="font-serif text-3xl tracking-tight text-ink">Choose a new password</h1>

      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm font-medium text-ink-2">New password</span>
          <input
            type="password"
            required
            minLength={6}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-line-strong"
          />
        </label>

        {error ? <p className="text-sm text-incorrect">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {submitting ? "Updating…" : "Update password"}
        </button>
      </form>
    </div>
  );
}
