"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { currentAnswers, fromRemoteCurrentAnswers, type RemoteCurrentAnswer } from "@/lib/attempts";
import { resolveQuestionMeta } from "@/lib/questionMeta";

interface SubjectSummary {
  slug: string;
  title: string;
  syllabusCode: string;
}

interface Props {
  subjects: SubjectSummary[];
}

interface SubjectCard extends SubjectSummary {
  attempted: number;
  correct: number;
}

export function DashboardOverview({ subjects }: Props) {
  // Signed out, attempts live in localStorage, unavailable during server
  // render — read in an effect either way so the markup matches on both
  // passes, and so signed-in state (also unknown at render time) has settled
  // before deciding which source to read.
  const [cards, setCards] = useState<SubjectCard[] | null>(null);
  const [failed, setFailed] = useState(false);
  const { user, supabase } = useAuth();

  useEffect(() => {
    if (user === undefined) return; // still resolving the session

    let cancelled = false;

    async function load() {
      const answers: Map<string, { isCorrect: boolean }> = new Map();

      if (user && supabase) {
        // Cross-device history: current_answers is already scoped to this
        // user by RLS (see 0024_secure_current_answers.sql), so no extra
        // filter is needed here.
        const { data } = await supabase
          .from("current_answers")
          .select("question_id, is_correct, time_spent_ms")
          .returns<RemoteCurrentAnswer[]>();
        for (const row of fromRemoteCurrentAnswers(data ?? [])) {
          answers.set(row.questionId, { isCorrect: row.isCorrect });
        }
      } else {
        for (const [questionId, attempt] of currentAnswers()) {
          answers.set(questionId, { isCorrect: attempt.isCorrect });
        }
      }

      // The server says which subject each attempted question belongs to; the
      // page no longer carries every question id of every subject to answer
      // that here. No attempts means no request.
      const meta = await resolveQuestionMeta(Array.from(answers.keys()));
      if (cancelled) return;

      const tally = new Map<string, { attempted: number; correct: number }>();
      for (const [questionId, attempt] of answers) {
        const subjectSlug = meta.get(questionId)?.subjectSlug;
        if (!subjectSlug) continue;
        const bucket = tally.get(subjectSlug) ?? { attempted: 0, correct: 0 };
        bucket.attempted += 1;
        if (attempt.isCorrect) bucket.correct += 1;
        tally.set(subjectSlug, bucket);
      }

      setCards(
        subjects.map((subject) => ({
          ...subject,
          attempted: tally.get(subject.slug)?.attempted ?? 0,
          correct: tally.get(subject.slug)?.correct ?? 0,
        })),
      );
    }

    load().catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [subjects, user, supabase]);

  if (failed) {
    return (
      <div className="mt-10 rounded-[2rem] border-2 border-dashed border-line-strong bg-surface/80 p-10 text-center">
        <p className="text-ink-2">Could not load your progress.</p>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-3">
          Your answers are safe. Check your connection and reload the page.
        </p>
      </div>
    );
  }

  if (cards === null) {
    return <div className="py-16 text-center text-ink-3">Loading…</div>;
  }

  return (
    <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map((card) => {
        const percentage =
          card.attempted > 0 ? Math.round((card.correct / card.attempted) * 100) : null;

        return (
          <Link
            key={card.slug}
            href={`/dashboard/${card.slug}` as Route}
            className="lift group rounded-2xl border-2 border-edge bg-surface p-5 shadow-[4px_4px_0_var(--pop)]"
          >
            <div className="flex items-baseline justify-between">
              <span className="text-lg font-bold text-ink">{card.title}</span>
              <span className="hl hl-blue font-mono text-xs">{card.syllabusCode}</span>
            </div>
            {percentage === null ? (
              <p className="mt-2 text-sm leading-relaxed text-ink-3">
                Nothing attempted yet.
              </p>
            ) : (
              <p className="mt-2 text-sm leading-relaxed text-ink-2">
                {card.correct} of {card.attempted} correct so far ·{" "}
                <span className="font-mono tabular-nums">{percentage}%</span>
              </p>
            )}
            <span className="mt-4 inline-block text-sm font-bold text-accent">
              View weak areas →
            </span>
          </Link>
        );
      })}
    </div>
  );
}
