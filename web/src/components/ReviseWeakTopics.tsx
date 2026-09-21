"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { McqArena } from "@/components/McqArena";
import {
  computeTopicStats,
  currentAnswers,
  fromRemoteCurrentAnswers,
  type RemoteCurrentAnswer,
  type TopicStat,
} from "@/lib/attempts";
import type { McqQuestion, Subject, Topic } from "@/lib/data/types";

// A topic needs enough attempts behind it to mean something — one unlucky
// guess should not brand a topic "weak" the same way a real pattern across a
// dozen questions does.
const MIN_ATTEMPTS = 3;
// Below this accuracy a topic is worth revising; at or above it, it isn't
// what a *weak*-topic queue exists for.
const WEAK_ACCURACY = 0.7;
// One sitting's worth, not a full re-drill of every weak topic in one go —
// the untimed per-topic drill already exists for that.
const MAX_TOPICS = 5;
const MAX_QUESTIONS = 20;

interface Props {
  subject: Subject;
  topics: Topic[];
  questionTopics: { id: string; topicCodes: string[] }[];
}

interface WeakTopic {
  topic: Topic;
  accuracy: number;
  attempted: number;
}

type QueueState =
  | { kind: "loading" }
  | { kind: "no-weak-topics" }
  | { kind: "empty"; weakTopics: WeakTopic[] }
  | { kind: "ready"; weakTopics: WeakTopic[]; questions: McqQuestion[] };

export function ReviseWeakTopics({ subject, topics, questionTopics }: Props) {
  const { user, supabase } = useAuth();
  const [state, setState] = useState<QueueState>({ kind: "loading" });

  const topicsByQuestion = useMemo(
    () => new Map(questionTopics.map((q) => [q.id, q.topicCodes])),
    [questionTopics],
  );
  const topicsByCode = useMemo(() => new Map(topics.map((t) => [t.code, t])), [topics]);

  useEffect(() => {
    if (user === undefined) return; // still resolving the session

    let cancelled = false;

    async function build() {
      // One read of the student's own attempts serves both halves of this:
      // which topics are weak (aggregated) and, once the question pool for
      // those topics comes back, which specific questions in it are the
      // wrong or never-tried ones worth putting in front of the student
      // first (per-question). Reading it once here, rather than through
      // Dashboard.tsx's separate per-topic-stat hook, is what keeps that
      // second lookup from being a duplicate network round trip.
      const answers = new Map<string, boolean>();
      if (user && supabase) {
        const { data } = await supabase
          .from("current_answers")
          .select("question_id, is_correct, time_spent_ms")
          .returns<RemoteCurrentAnswer[]>();
        for (const row of fromRemoteCurrentAnswers(data ?? [])) {
          answers.set(row.questionId, row.isCorrect);
        }
      } else {
        for (const [questionId, attempt] of currentAnswers()) {
          answers.set(questionId, attempt.isCorrect);
        }
      }
      if (cancelled) return;

      const stats = computeTopicStats(
        Array.from(answers).map(([questionId, isCorrect]) => ({
          questionId,
          isCorrect,
          timeSpentMs: 0,
        })),
        topicsByQuestion,
      );

      const weakTopics: WeakTopic[] = Array.from(stats)
        .map(([code, stat]): { topic: Topic | undefined; stat: TopicStat } => ({
          topic: topicsByCode.get(code),
          stat,
        }))
        .filter(
          (row): row is { topic: Topic; stat: TopicStat } =>
            row.topic !== undefined &&
            row.stat.attempted >= MIN_ATTEMPTS &&
            row.stat.accuracy < WEAK_ACCURACY,
        )
        .sort((a, b) => a.stat.accuracy - b.stat.accuracy)
        .slice(0, MAX_TOPICS)
        .map(({ topic, stat }) => ({ topic, accuracy: stat.accuracy, attempted: stat.attempted }));

      if (weakTopics.length === 0) {
        if (!cancelled) setState({ kind: "no-weak-topics" });
        return;
      }

      const params = new URLSearchParams({
        subject: subject.slug,
        topics: weakTopics.map((w) => w.topic.code).join(","),
      });
      const res = await fetch(`/api/revision-queue?${params.toString()}`);
      const body: { questions?: McqQuestion[] } = await res.json();
      if (cancelled) return;

      const pool = body.questions ?? [];
      // Worst-served-first: a question already answered correctly is not
      // weak regardless of which topic it's filed under, and one answered
      // wrong belongs ahead of one never attempted.
      const wrong = pool.filter((q) => answers.get(q.id) === false);
      const unseen = pool.filter((q) => !answers.has(q.id));
      const queue = [...wrong, ...unseen].slice(0, MAX_QUESTIONS);

      setState(
        queue.length > 0 ? { kind: "ready", weakTopics, questions: queue } : { kind: "empty", weakTopics },
      );
    }

    void build();
    return () => {
      cancelled = true;
    };
  }, [user, supabase, subject.slug, topicsByQuestion, topicsByCode]);

  if (state.kind === "loading") {
    return <div className="py-16 text-center text-ink-3">Building your revision queue…</div>;
  }

  if (state.kind === "no-weak-topics") {
    return (
      <div className="rounded-[2rem] border-2 border-dashed border-line-strong bg-surface/80 p-10 text-center">
        <h1 className="font-serif text-2xl font-extrabold tracking-tight text-ink">Nothing weak to revise yet</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-3">
          This fills in once you have attempted at least {MIN_ATTEMPTS} questions on a topic and
          scored under {Math.round(WEAK_ACCURACY * 100)}% on it. Sit a paper or run a topic drill
          first.
        </p>
        <Link
          href={`/dashboard/${subject.slug}` as Route}
          className="pill pill-solid mt-5 px-5 py-2 text-sm"
        >
          Back to dashboard →
        </Link>
      </div>
    );
  }

  const weakList = (
    <p className="mb-6 text-sm leading-relaxed text-ink-2">
      Revising{" "}
      {state.weakTopics
        .map((w) => `${w.topic.title} (${Math.round(w.accuracy * 100)}%)`)
        .join(", ")}
      .
    </p>
  );

  if (state.kind === "empty") {
    return (
      <div>
        {weakList}
        <div className="rounded-[2rem] border-2 border-dashed border-line-strong bg-surface/80 p-10 text-center">
          <p className="text-ink-2">
            Every question on these topics is either already correct or unavailable right now.
          </p>
          <Link
            href={`/dashboard/${subject.slug}` as Route}
            className="pill pill-solid mt-5 px-5 py-2 text-sm"
          >
            Back to dashboard →
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-1 font-serif text-3xl font-extrabold tracking-tight text-ink">
        {subject.title} — weak-topic revision
      </h1>
      {weakList}
      <McqArena
        sessionKey={`revise:${subject.slug}`}
        title={`${subject.title} — weak-topic revision`}
        questions={state.questions}
        backHref={`/dashboard/${subject.slug}` as Route}
        backLabel="Back to dashboard"
        secondsPerQuestion={null}
      />
    </div>
  );
}
