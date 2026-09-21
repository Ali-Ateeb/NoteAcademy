"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { LogoStacked } from "@/components/Brand";
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
      <div className="mx-4 my-12 max-w-sm min-[420px]:mx-auto rounded-[2rem] border-2 border-edge bg-surface px-7 py-9 shadow-[4px_4px_0_var(--pop)] text-center">
        <div className="mb-6 flex justify-center">
          <LogoStacked className="h-20" />
        </div>
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
      <div className="mx-4 my-12 max-w-sm min-[420px]:mx-auto rounded-[2rem] border-2 border-edge bg-surface px-7 py-9 shadow-[4px_4px_0_var(--pop)] text-center">
        <div className="mb-6 flex justify-center">
          <LogoStacked className="h-20" />
        </div>
        <h1 className="font-serif text-3xl tracking-tight text-ink">Link expired</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-2">
          This password reset link is no longer valid. Request a new one from the
          sign-in page.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-4 my-12 max-w-sm min-[420px]:mx-auto rounded-[2rem] border-2 border-edge bg-surface px-7 py-9 shadow-[4px_4px_0_var(--pop)]">
        <div className="mb-6 flex justify-center">
          <LogoStacked className="h-20" />
        </div>
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
            className="w-full rounded-xl border-2 border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-accent"
          />
        </label>

        {error ? <p className="text-sm text-incorrect">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting}
          className="pill pill-solid w-full justify-center px-4 py-2.5 text-sm disabled:opacity-60"
        >
          {submitting ? "Updating…" : "Update password"}
        </button>
      </form>
    </div>
  );
}
