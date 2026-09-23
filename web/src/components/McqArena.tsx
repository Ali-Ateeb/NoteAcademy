"use client";

import type { Route } from "next";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { PencilMark } from "@/components/Brand";
import { SolutionButton } from "@/components/SolutionButton";
import {
  appendAttempts,
  clearSession,
  formatClock,
  formatDuration,
  loadSession,
  saveSession,
  syncAttemptsToServer,
  type SessionState,
} from "@/lib/attempts";
import type { McqOption, McqQuestion } from "@/lib/data/types";

const OPTIONS: McqOption[] = ["A", "B", "C", "D"];
/** CAIE allows roughly 75 seconds per mark on Paper 1. */
export const SECONDS_PER_QUESTION = 75;

interface Props {
  /** Identifies the saved session. A paper slug when sitting a paper, a topic
   *  key when drilling — distinct keys mean a drill never clobbers a
   *  half-finished exam. */
  sessionKey: string;
  title: string;
  questions: McqQuestion[];
  /** Where "leave" and "back" go. Typed, so typedRoutes catches a dead link
   *  at build time rather than in front of a student. */
  backHref: Route;
  backLabel?: string;
  /** Seconds per question, or null for an untimed drill. Drilling a weak topic
   *  is about getting it right, not about beating a clock; a timer there just
   *  adds pressure to the thing the student is already worst at. */
  secondsPerQuestion?: number | null;
}

export function McqArena({
  sessionKey,
  title,
  questions,
  backHref,
  backLabel = "Leave test",
  secondsPerQuestion = SECONDS_PER_QUESTION,
}: Props) {
  const [state, setState] = useState<SessionState | null>(null);
  const [resumed, setResumed] = useState(false);
  const { user, supabase } = useAuth();
  const questionEnteredAt = useRef<number>(Date.now());
  /** Time accumulated per question across visits — a student who returns to
   *  question 3 three times has spent all of it on question 3. */
  const timeSpent = useRef<Map<string, number>>(new Map());

  const total = questions.length;
  const timed = secondsPerQuestion !== null;
  const duration = timed ? total * secondsPerQuestion : 0;

  useEffect(() => {
    const saved = loadSession(sessionKey);
    if (saved && !saved.submitted && saved.questionIds.length === total) {
      setState(saved);
      setResumed(true);
      return;
    }
    setState({
      sessionKey,
      questionIds: questions.map((q) => q.id),
      answers: {},
      flagged: [],
      currentIndex: 0,
      startedAt: Date.now(),
      remainingSeconds: duration,
      submitted: false,
    });
  }, [sessionKey, questions, total, duration]);

  useEffect(() => {
    if (state) saveSession(state);
  }, [state]);

  // The clock only runs while the test is open and unsubmitted. Time spent away
  // is not charged: losing connectivity should not cost a student the paper.
  //
  // This depends on `running` alone, never on `state`. Depending on the whole
  // state object tears the interval down and recreates it on every keystroke,
  // so a student answering quickly would watch the clock sit still.
  const running = timed && state !== null && !state.submitted;

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setState((prev) => {
        if (!prev || prev.submitted) return prev;
        const remaining = prev.remainingSeconds - 1;
        return remaining <= 0
          ? { ...prev, remainingSeconds: 0, submitted: true }
          : { ...prev, remainingSeconds: remaining };
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [running]);

  const current = state ? questions[state.currentIndex] : undefined;

  const chargeTime = useCallback((questionId: string) => {
    const elapsed = Date.now() - questionEnteredAt.current;
    timeSpent.current.set(
      questionId,
      (timeSpent.current.get(questionId) ?? 0) + elapsed,
    );
    questionEnteredAt.current = Date.now();
  }, []);

  const goTo = useCallback(
    (index: number) => {
      setState((prev) => {
        if (!prev || index < 0 || index >= total) return prev;
        const leaving = questions[prev.currentIndex];
        if (leaving) chargeTime(leaving.id);
        return { ...prev, currentIndex: index };
      });
    },
    [chargeTime, questions, total],
  );

  function select(option: McqOption) {
    setState((prev) =>
      prev && current && !prev.submitted
        ? { ...prev, answers: { ...prev.answers, [current.id]: option } }
        : prev,
    );
  }

  function toggleFlag() {
    setState((prev) => {
      if (!prev || !current) return prev;
      const flagged = prev.flagged.includes(current.id)
        ? prev.flagged.filter((id) => id !== current.id)
        : [...prev.flagged, current.id];
      return { ...prev, flagged };
    });
  }

  const submit = useCallback(() => {
    setState((prev) => {
      if (!prev || prev.submitted) return prev;
      const leaving = questions[prev.currentIndex];
      if (leaving) chargeTime(leaving.id);

      const records = questions
        .filter((question) => prev.answers[question.id])
        .map((question) => ({
          questionId: question.id,
          paperSlug: question.paperSlug,
          selectedOption: prev.answers[question.id] as string,
          isCorrect: prev.answers[question.id] === question.correctOption,
          timeSpentMs: timeSpent.current.get(question.id) ?? 0,
          createdAt: Date.now(),
        }));

      appendAttempts(records);
      // Local storage first, always — the mirror below is best-effort and
      // must never be what the arena's own UI depends on to show a result.
      if (user && supabase) void syncAttemptsToServer(supabase, user.id, records);

      return { ...prev, submitted: true };
    });
  }, [chargeTime, questions, user, supabase]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!state || state.submitted || event.metaKey || event.ctrlKey) return;
      const key = event.key.toUpperCase();
      if ((OPTIONS as string[]).includes(key)) {
        event.preventDefault();
        select(key as McqOption);
      }
      if (event.key === "ArrowRight") goTo(state.currentIndex + 1);
      if (event.key === "ArrowLeft") goTo(state.currentIndex - 1);
      if (event.key.toLowerCase() === "f") toggleFlag();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const score = useMemo(() => {
    if (!state) return 0;
    return questions.filter((q) => state.answers[q.id] === q.correctOption).length;
  }, [questions, state]);

  if (!state || !current) {
    return <div className="py-20 text-center text-ink-3">Loading…</div>;
  }

  const answeredCount = Object.keys(state.answers).length;
  const lowTime = timed && state.remainingSeconds < 60 && !state.submitted;

  if (state.submitted) {
    return (
      <Results
        backHref={backHref}
        title={title}
        questions={questions}
        answers={state.answers}
        score={score}
        timeSpent={timeSpent.current}
        onRetake={() => {
          clearSession(sessionKey);
          timeSpent.current = new Map();
          setState({
            sessionKey,
            questionIds: questions.map((q) => q.id),
            answers: {},
            flagged: [],
            currentIndex: 0,
            startedAt: Date.now(),
            remainingSeconds: duration,
            submitted: false,
          });
        }}
      />
    );
  }

  return (
    <div>
      {resumed && (
        <div className="mb-4 rounded-xl border border-note-line bg-note px-4 py-2.5 text-sm text-ink">
          Resumed where you left off — {answeredCount} of {total} answered.
        </div>
      )}

      <div className="mb-5 flex flex-wrap items-center gap-4">
        {timed && (
          <span
            className={`rounded-full border-2 px-4 py-1 font-mono text-lg tabular-nums ${
              lowTime
                ? "border-incorrect bg-incorrect-soft text-incorrect"
                : "border-edge bg-surface text-ink"
            }`}
            aria-live={lowTime ? "polite" : "off"}
          >
            {formatClock(state.remainingSeconds)}
          </span>
        )}
        <span className="text-sm text-ink-2">
          {answeredCount} of {total} answered
        </span>
        <button
          type="button"
          onClick={submit}
          className="pill pill-solid ml-auto px-5 py-2 text-sm"
        >
          {timed ? "Submit paper" : "Mark answers"}
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_200px]">
        {/* Keyed on the question, so moving between questions re-runs the
            entrance: each one arrives rather than swapping in place, which is
            what makes a 40-question paper feel paced instead of abrupt. */}
        <div
          key={current.id}
          className="animate-rise rounded-2xl border border-line-strong bg-surface p-6 shadow-card"
        >
          <div className="mb-4 flex items-center justify-between">
            <span className="hl hl-blue gutter-num !text-ink">
              Question {state.currentIndex + 1} / {total}
            </span>
            <button
              type="button"
              onClick={toggleFlag}
              className={`rounded-full px-3 py-1 text-xs font-bold transition-colors ${
                state.flagged.includes(current.id)
                  ? "bg-hl-yellow text-ink"
                  : "text-ink-3 hover:bg-surface-2 hover:text-ink"
              }`}
            >
              {state.flagged.includes(current.id) ? "Flagged" : "Flag for review"}
            </button>
          </div>

          {/* Text only when there is no crop to show instead -- the crop is
              the question as printed, stem and all, so the extracted text
              above it was showing the same words twice. */}
          {current.questionText && !current.cropUrl && (
            <p className="text-[15px] leading-relaxed text-ink">{current.questionText}</p>
          )}

          {current.cropUrl && (
            /* The question as printed. For a paper ingested geometrically this
               is the whole question — text extraction scrambles the reading
               order and loses every diagram, and a 2015 circuit question uses
               four circuit diagrams as its options. White regardless of theme:
               it is a page, not part of the interface. */
            <div
              className="overflow-hidden rounded-xl border border-line bg-white"
              // Reserves the crop's own height before the file arrives, from
              // its bbox — otherwise the page grows underneath the options
              // below it on every load.
              style={current.cropAspectRatio ? { aspectRatio: current.cropAspectRatio } : undefined}
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- signed
                  URL on a bucket host, resolved per request. */}
              <img
                src={current.cropUrl}
                alt={`Question ${current.displayLabel}, as printed`}
                className="w-full"
              />
            </div>
          )}

          <div className="mt-6 space-y-2" role="radiogroup" aria-label="Answer options">
            {OPTIONS.map((option) => {
              const selected = state.answers[current.id] === option;
              return (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => select(option)}
                  className={`flex w-full items-start gap-3 rounded-xl border-2 px-4 py-3 text-left transition-all duration-150 ease-[cubic-bezier(0.45,0,0.55,1)] active:scale-[0.995] ${
                    selected
                      ? "border-accent bg-accent-soft"
                      : "border-line hover:border-accent hover:bg-accent-soft/50"
                  }`}
                >
                  <span
                    className={`mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-xs font-medium transition-colors duration-150 ${
                      selected ? "bg-accent text-accent-ink" : "bg-hl-yellow text-ink"
                    }`}
                  >
                    {option}
                  </span>
                  {/* Nothing beside the letter when the options are printed
                      in the crop above. An empty string here would render as a
                      blank line that looks like a failed load. */}
                  {current.options[option] && (
                    <span className="text-sm leading-relaxed text-ink">
                      {current.options[option]}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-6 flex items-center justify-between border-t border-line pt-4">
            <button
              type="button"
              onClick={() => goTo(state.currentIndex - 1)}
              disabled={state.currentIndex === 0}
              className="pill pill-plain px-4 py-1.5 text-sm disabled:pointer-events-none disabled:opacity-40"
            >
              ← Previous
            </button>
            <span className="hidden text-xs text-ink-3 sm:inline">
              <kbd className="rounded border border-line px-1">A</kbd>–
              <kbd className="rounded border border-line px-1">D</kbd> answer ·{" "}
              <kbd className="rounded border border-line px-1">F</kbd> flag ·{" "}
              <kbd className="rounded border border-line px-1">←→</kbd> navigate
            </span>
            <button
              type="button"
              onClick={() => goTo(state.currentIndex + 1)}
              disabled={state.currentIndex === total - 1}
              className="pill pill-plain px-4 py-1.5 text-sm disabled:pointer-events-none disabled:opacity-40"
            >
              Next →
            </button>
          </div>
        </div>

        <aside>
          <h2 className="mb-3 text-xs font-bold uppercase tracking-widest text-ink-2">
            Questions
          </h2>
          <div className="grid grid-cols-8 gap-1.5 lg:grid-cols-5">
            {questions.map((question, index) => {
              const answered = Boolean(state.answers[question.id]);
              const flagged = state.flagged.includes(question.id);
              const active = index === state.currentIndex;
              return (
                <button
                  key={question.id}
                  type="button"
                  onClick={() => goTo(index)}
                  aria-label={`Question ${index + 1}${answered ? ", answered" : ""}${flagged ? ", flagged" : ""}`}
                  className={`relative aspect-square rounded-lg border-2 font-mono text-xs transition-all duration-150 hover:scale-105 ${
                    active
                      ? "border-accent bg-accent text-accent-ink"
                      : answered
                        ? "border-transparent bg-hl-blue text-ink"
                        : "border-line bg-surface text-ink-3 hover:border-accent hover:text-ink"
                  }`}
                >
                  {index + 1}
                  {flagged && (
                    <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-marks" />
                  )}
                </button>
              );
            })}
          </div>
          <Link
            href={backHref}
            className="mt-5 inline-block text-sm text-ink-3 transition-colors hover:text-ink"
          >
            ← {backLabel}
          </Link>
        </aside>
      </div>
    </div>
  );
}

function Results({
  backHref,
  title,
  questions,
  answers,
  score,
  timeSpent,
  onRetake,
}: {
  backHref: Route;
  title: string;
  questions: McqQuestion[];
  answers: Record<string, string>;
  score: number;
  timeSpent: Map<string, number>;
  onRetake: () => void;
}) {
  const percentage = Math.round((score / questions.length) * 100);
  const totalTime = [...timeSpent.values()].reduce((sum, ms) => sum + ms, 0);

  return (
    <div>
      <div className="animate-rise relative rounded-2xl border-2 border-edge bg-surface p-7 shadow-[4px_4px_0_var(--pop)]">
        <PencilMark className="absolute -top-5 right-6 h-14 rotate-6" />
        <p className="hl hl-yellow gutter-num w-fit !text-ink">{title}</p>
        <div className="mt-3 flex flex-wrap items-end gap-8">
          <div>
            {/* The score lands rather than appears — the one moment in the
                app where a small piece of theatre is actually earned. */}
            <span className="animate-stamp block font-serif text-5xl tabular-nums text-ink">
              {score}
              <span className="text-2xl text-ink-3">/{questions.length}</span>
            </span>
            <p className="mt-1 text-sm text-ink-2">{percentage}% correct</p>
          </div>
          <div>
            <span className="font-mono text-2xl tabular-nums text-ink">
              {formatDuration(totalTime)}
            </span>
            <p className="mt-1 text-sm text-ink-2">time spent</p>
          </div>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={onRetake}
              className="pill pill-plain px-4 py-2 text-sm"
            >
              Retake
            </button>
            <Link
              href="/dashboard"
              className="pill pill-solid px-4 py-2 text-sm"
            >
              See weak areas
            </Link>
          </div>
        </div>
      </div>

      <h2 className="rule-under mb-5 mt-10 font-serif text-2xl tracking-tight text-ink">
        Every question
      </h2>
      <div className="space-y-3">
        {questions.map((question, index) => {
          const chosen = answers[question.id];
          const correct = chosen === question.correctOption;

          return (
            <div
              key={question.id}
              className="rounded-2xl border border-line bg-surface p-5 shadow-card"
            >
              <div className="flex items-start gap-3">
                <span
                  className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md font-mono text-xs ${
                    !chosen
                      ? "bg-surface-2 text-ink-3"
                      : correct
                        ? "bg-correct-soft text-correct"
                        : "bg-incorrect-soft text-incorrect"
                  }`}
                >
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-relaxed text-ink">
                    {question.questionText}
                  </p>

                  <p className="mt-3 text-sm">
                    {chosen ? (
                      <>
                        <span className={correct ? "text-correct" : "text-incorrect"}>
                          You answered {chosen}
                        </span>
                        {!correct && (
                          <span className="text-ink-2">
                            {" "}
                            · correct answer {question.correctOption}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-ink-3">
                        Not answered · correct answer {question.correctOption}
                      </span>
                    )}
                    {timeSpent.get(question.id) ? (
                      <span className="ml-2 font-mono text-xs text-ink-3">
                        {formatDuration(timeSpent.get(question.id) ?? 0)}
                      </span>
                    ) : null}
                  </p>

                  {(question.markScheme || question.examinerNote) && (
                  <div className="mt-3 rounded-xl bg-surface-2 p-4">
                    {question.markScheme && (
                      <>
                        <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
                          Mark scheme
                        </p>
                        <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                          {question.markScheme}
                        </p>
                      </>
                    )}

                    {question.examinerNote && (
                      <>
                        <p className={`${question.markScheme ? "mt-4 " : ""}text-xs font-semibold uppercase tracking-widest text-marks`}>
                          Examiner report
                        </p>
                        <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
                          {question.examinerNote}
                        </p>
                      </>
                    )}
                  </div>
                  )}

                  <SolutionButton questionId={question.id} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <Link
        href={backHref}
        className="mt-8 inline-block text-sm text-ink-3 transition-colors hover:text-ink"
      >
        ← Back
      </Link>
    </div>
  );
}
