"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { currentAnswers, fromRemoteCurrentAnswers, type RemoteCurrentAnswer } from "@/lib/attempts";

interface SubjectSummary {
  slug: string;
  title: string;
  syllabusCode: string;
  questionIds: string[];
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

      if (cancelled) return;
      setCards(
        subjects.map((subject) => {
          const ids = new Set(subject.questionIds);
          let attempted = 0;
          let correct = 0;
          for (const [questionId, attempt] of answers) {
            if (!ids.has(questionId)) continue;
            attempted += 1;
            if (attempt.isCorrect) correct += 1;
          }
          return { ...subject, attempted, correct };
        }),
      );
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [subjects, user, supabase]);

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
            className="group rounded-2xl border border-line bg-surface p-5 shadow-card transition-all hover:border-line-strong hover:shadow-lift"
          >
            <div className="flex items-baseline justify-between">
              <span className="font-medium tracking-tight text-ink">{card.title}</span>
              <span className="font-mono text-xs text-ink-3">{card.syllabusCode}</span>
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
            <span className="mt-4 inline-block text-sm font-medium text-accent">
              View weak areas →
            </span>
          </Link>
        );
      })}
    </div>
  );
}
