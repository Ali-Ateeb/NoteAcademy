"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";

export default function SignupPage() {
  const { supabase } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checkInbox, setCheckInbox] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabase) return;
    setSubmitting(true);
    setError(null);

    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });

    setSubmitting(false);
    if (signUpError) {
      setError(signUpError.message);
      return;
    }

    // A project with email confirmation off returns a live session
    // immediately; one with it on returns a user but no session, and the
    // account only becomes usable once the confirmation link is clicked.
    if (data.session) {
      router.push("/dashboard");
      router.refresh();
    } else {
      setCheckInbox(true);
    }
  }

  if (checkInbox) {
    return (
      <div className="mx-auto max-w-sm px-5 py-16 text-center">
        <h1 className="font-serif text-3xl tracking-tight text-ink">Check your email</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-2">
          We&apos;ve sent a confirmation link to <strong>{email}</strong>. Click it to
          finish creating your account.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm px-5 py-16">
      <h1 className="font-serif text-3xl tracking-tight text-ink">Create an account</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-2">
        Practice you&apos;ve already done on this device comes with you.
      </p>

      {!supabase ? (
        <p className="mt-8 rounded-xl border border-line bg-surface p-4 text-sm text-ink-3">
          Accounts aren&apos;t configured for this app instance. Progress is
          being kept on this device only.
        </p>
      ) : (
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
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-ink-2">Password</span>
            <input
              type="password"
              required
              minLength={6}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-line-strong"
            />
            <span className="mt-1.5 block text-xs text-ink-3">At least 6 characters.</span>
          </label>

          {error ? <p className="text-sm text-incorrect">{error}</p> : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {submitting ? "Creating account…" : "Create account"}
          </button>

          <p className="text-sm text-ink-3">
            Already have an account?{" "}
            <Link href="/login" className="text-ink hover:underline">
              Sign in
            </Link>
          </p>
        </form>
      )}
    </div>
  );
}
