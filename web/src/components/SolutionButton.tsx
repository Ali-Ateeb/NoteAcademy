"use client";

import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";

/**
 * Reveals a full worked solution for one question, fetched from /api/solve on
 * click rather than alongside the mark scheme — a real LLM call is behind it,
 * so it only runs for a question a student actually asks about, and the
 * result is cached server-side after the first request.
 *
 * Signed out renders nothing: the route requires a session (it meters against
 * a per-user daily quota), and a button that always fails to sign-in is worse
 * than no button.
 */
export function SolutionButton({ questionId }: { questionId: string }) {
  const { user, supabase } = useAuth();
  const [state, setState] = useState<
    { kind: "idle" } | { kind: "loading" } | { kind: "done"; solution: string } | { kind: "error"; message: string }
  >({ kind: "idle" });

  if (!supabase || !user) return null;

  async function fetchSolution() {
    setState({ kind: "loading" });
    try {
      const res = await fetch("/api/solve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ questionId }),
      });
      const body = await res.json();
      if (!res.ok) {
        setState({ kind: "error", message: body.error ?? "Something went wrong." });
        return;
      }
      setState({ kind: "done", solution: body.solution as string });
    } catch {
      setState({ kind: "error", message: "Could not reach the solver." });
    }
  }

  if (state.kind === "done") {
    return (
      <div className="mt-3 rounded-xl border border-accent/30 bg-accent-soft p-4">
        <p className="text-xs font-bold uppercase tracking-widest text-accent">
          Worked solution
        </p>
        <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-ink">
          {state.solution}
        </p>
      </div>
    );
  }

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={fetchSolution}
        disabled={state.kind === "loading"}
        className="pill pill-plain px-3.5 py-1.5 text-xs disabled:pointer-events-none disabled:opacity-60"
      >
        {state.kind === "loading" ? "Thinking…" : "Get worked solution"}
      </button>
      {state.kind === "error" && (
        <p className="mt-1.5 text-xs text-incorrect">{state.message}</p>
      )}
    </div>
  );
}
