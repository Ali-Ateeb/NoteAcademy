"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { setReviewToken } from "@/lib/review";

/**
 * What /admin/review renders instead of the queue when the read cookie
 * (`/api/admin-auth`) is missing or wrong. The Server Component never fetches
 * the queue in that case — this form is the only thing on the page — so a
 * request that has not passed this gate never causes an unapproved
 * question's text, mark scheme or crop to be read at all.
 *
 * Also calls `setReviewToken`, the same localStorage the write path
 * (`@/lib/review`) already reads: entering the token once satisfies both
 * gates, rather than asking a reviewer to type it again to unlock saving.
 */
export function AdminGate() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/admin-auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { error?: string };
        setError(body.error ?? `Check failed (${response.status}).`);
        return;
      }
      setReviewToken(token);
      // The cookie is httpOnly and was just set by the response above; a
      // client-side redirect cannot see it early, so re-run the Server
      // Component rather than navigate — this *is* the same page, now with
      // the cookie the request carries.
      router.refresh();
    } catch (err) {
      setError(`Check failed: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm px-5 py-16">
      <h1 className="font-serif text-3xl tracking-tight text-ink">Review queue</h1>
      <p className="mt-3 text-sm leading-relaxed text-ink-2">
        This page holds unapproved questions — crops, mark schemes, correct
        options — none of which is meant to be public yet. Enter the review
        token to continue.
      </p>

      <form onSubmit={onSubmit} className="mt-8 space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm font-medium text-ink-2">Review token</span>
          <input
            type="password"
            required
            autoFocus
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-line-strong"
          />
        </label>

        {error ? <p className="text-sm text-incorrect">{error}</p> : null}

        <button
          type="submit"
          disabled={submitting || !token}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {submitting ? "Checking…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
