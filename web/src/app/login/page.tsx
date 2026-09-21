"use client";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { LogoStacked } from "@/components/Brand";
import { useAuth } from "@/components/AuthProvider";
import { safeNextPath } from "@/lib/safeRedirect";

// useSearchParams() opts the tree under it out of static prerendering unless
// wrapped in Suspense — without this, `next build` fails prerendering
// "/login" outright rather than degrading gracefully.
export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const { supabase } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabase) return;
    setSubmitting(true);
    setError(null);

    const { error: signInError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    setSubmitting(false);
    if (signInError) {
      setError(signInError.message);
      return;
    }
    router.push(safeNextPath(searchParams.get("next"), "/dashboard") as Route);
    router.refresh();
  }

  return (
    <div className="mx-4 my-12 max-w-sm min-[420px]:mx-auto rounded-[2rem] border-2 border-edge bg-surface px-7 py-9 shadow-[4px_4px_0_var(--pop)]">
        <div className="mb-6 flex justify-center">
          <LogoStacked className="h-20" />
        </div>
      <h1 className="font-serif text-3xl tracking-tight text-ink">Sign in</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-2">
        Your progress follows you to any device once you&apos;re signed in.
      </p>

      {!supabase ? (
        <p className="mt-8 rounded-xl border border-line bg-surface p-4 text-sm text-ink-3">
          Accounts aren&apos;t configured for this app instance. Progress is
          being kept on this device only.
        </p>
      ) : (
        <form onSubmit={onSubmit} className="mt-8 space-y-4">
          <Field label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" />
          <Field
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
          />

          {error ? <p className="text-sm text-incorrect">{error}</p> : null}

          <button
            type="submit"
            disabled={submitting}
            className="pill pill-solid w-full justify-center px-4 py-2.5 text-sm disabled:opacity-60"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>

          <div className="flex justify-between text-sm text-ink-3">
            <Link href="/signup" className="hover:text-ink">
              Create an account
            </Link>
            <Link href="/forgot-password" className="hover:text-ink">
              Forgot password?
            </Link>
          </div>
        </form>
      )}
    </div>
  );
}

function Field({
  label,
  type,
  value,
  onChange,
  autoComplete,
}: {
  label: string;
  type: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-ink-2">{label}</span>
      <input
        type={type}
        required
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border-2 border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-accent"
      />
    </label>
  );
}
