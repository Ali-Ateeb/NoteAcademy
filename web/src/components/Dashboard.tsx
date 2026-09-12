"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import {
  computeTopicStats,
  formatDuration,
  fromRemoteCurrentAnswers,
  topicStats,
  type RemoteCurrentAnswer,
  type TopicStat,
} from "@/lib/attempts";
import type { Topic } from "@/lib/data/types";

interface Props {
  subjectSlug: string;
  topics: Topic[];
  questionTopics: { id: string; topicCodes: string[] }[];
}

export function Dashboard({ subjectSlug, topics, questionTopics }: Props) {
  // Signed out, attempts live in localStorage, unavailable during server
  // render; signed in, they live behind a database round trip. Either way
  // this settles in an effect, so the markup matches on both passes.
  const [stats, setStats] = useState<Map<string, TopicStat> | null>(null);
  const { user, supabase } = useAuth();

  const topicsByQuestion = useMemo(
    () => new Map(questionTopics.map((q) => [q.id, q.topicCodes])),
    [questionTopics],
  );

  useEffect(() => {
    if (user === undefined) return; // still resolving the session

    if (!user || !supabase) {
      setStats(topicStats(topicsByQuestion));
      return;
    }

    let cancelled = false;
    supabase
      .from("current_answers")
      .select("question_id, is_correct, time_spent_ms")
      .returns<RemoteCurrentAnswer[]>()
      .then(({ data }) => {
        if (cancelled) return;
        setStats(computeTopicStats(fromRemoteCurrentAnswers(data ?? []), topicsByQuestion));
      });
    return () => {
      cancelled = true;
    };
  }, [topicsByQuestion, user, supabase]);

  if (stats === null) {
    return <div className="py-16 text-center text-ink-3">Loading…</div>;
  }

  const rows = topics
    .map((topic) => ({ topic, stat: stats.get(topic.code) }))
    .filter((row): row is { topic: Topic; stat: TopicStat } => row.stat !== undefined)
    .sort((a, b) => a.stat.accuracy - b.stat.accuracy);

  if (rows.length === 0) {
    return (
      <div className="mt-10 rounded-2xl border border-dashed border-line p-10 text-center">
        <p className="text-ink-2">Nothing to show yet.</p>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-3">
          Sit a paper and this fills with accuracy per topic and time per question.
        </p>
        <Link
          href={`/subjects/${subjectSlug}` as Route}
          className="mt-5 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
        >
          Browse papers →
        </Link>
      </div>
    );
  }

  const attempted = rows.reduce((sum, row) => sum + row.stat.attempted, 0);
  const correct = rows.reduce((sum, row) => sum + row.stat.correct, 0);

  return (
    <>
      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        <Stat label="Questions attempted" value={String(attempted)} />
        <Stat
          label="Overall accuracy"
          value={`${Math.round((correct / attempted) * 100)}%`}
        />
        <Stat label="Topics covered" value={`${rows.length} / ${topics.length}`} />
      </div>

      <div className="mt-8 space-y-2">
        {rows.map(({ topic, stat }) => {
          const percentage = Math.round(stat.accuracy * 100);
          // Bars are coloured by meaning, not by rank: below 50% is a problem
          // regardless of whether it happens to be the worst topic today.
          const tone =
            percentage >= 75
              ? "bg-correct"
              : percentage >= 50
                ? "bg-marks"
                : "bg-incorrect";

          return (
            <Link
              key={topic.code}
              href={`/topics/${subjectSlug}/${topic.slug}/practice` as Route}
              className="block rounded-xl border border-line bg-surface p-4 shadow-card transition-colors hover:border-line-strong"
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm text-ink">
                  <span className="mr-2 font-mono text-xs text-ink-3">{topic.code}</span>
                  {topic.title}
                </span>
                <span className="shrink-0 font-mono text-sm tabular-nums text-ink">
                  {percentage}%
                </span>
              </div>

              <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div
                  className={`h-full rounded-full ${tone}`}
                  style={{ width: `${percentage}%` }}
                />
              </div>

              <p className="mt-2 text-xs text-ink-3">
                {stat.correct} of {stat.attempted} correct · median{" "}
                {formatDuration(stat.medianMs)} per question
              </p>
            </Link>
          );
        })}
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <p className="font-mono text-2xl tabular-nums text-ink">{value}</p>
      <p className="mt-1 text-xs text-ink-3">{label}</p>
    </div>
  );
}
