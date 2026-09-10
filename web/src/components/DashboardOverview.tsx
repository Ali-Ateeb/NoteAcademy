"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState } from "react";

import { currentAnswers } from "@/lib/attempts";

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
  // Attempts live in localStorage, unavailable during server render — read
  // them in an effect so the markup matches on both passes.
  const [cards, setCards] = useState<SubjectCard[] | null>(null);

  useEffect(() => {
    const answers = currentAnswers();
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
  }, [subjects]);

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
