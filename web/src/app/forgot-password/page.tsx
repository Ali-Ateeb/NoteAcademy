"use client";

import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";

export default function ForgotPasswordPage() {
  const { supabase } = useAuth();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabase) return;
    setSubmitting(true);
    setError(null);

    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
    });

    setSubmitting(false);
    // Shown regardless of whether the email exists — confirming or denying an
    // account's existence to an unauthenticated caller is its own leak.
    if (!resetError) setSent(true);
    else setError(resetError.message);
  }

  if (sent) {
    return (
      <div className="mx-auto max-w-sm px-5 py-16 text-center">
        <h1 className="font-serif text-3xl tracking-tight text-ink">Check your email</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-2">
          If an account exists for <strong>{email}</strong>, a password reset link is
          on its way.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm px-5 py-16">
      <h1 className="font-serif text-3xl tracking-tight text-ink">Reset your password</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-2">
        We&apos;ll email you a link to choose a new one.
      </p>

      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm font-medium text-ink-2">Email</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-line-strong"
          />
        </label>

        {error ? <p className="text-sm text-incorrect">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting || !supabase}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {submitting ? "Sending…" : "Send reset link"}
        </button>
      </form>
    </div>
  );
}
