"use client";

import type { Route } from "next";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { SolutionButton } from "@/components/SolutionButton";
import {
  appendStructuredAttempts,
  clearStructuredSession,
  formatDuration,
  loadStructuredSession,
  saveStructuredSession,
  syncStructuredAttemptsToServer,
  type StructuredSessionState,
} from "@/lib/attempts";
import type { StructuredPart, StructuredQuestion } from "@/lib/data/types";

interface Props {
  /** Identifies the saved session — a paper slug, the same as the mcq arena. */
  sessionKey: string;
  title: string;
  questions: StructuredQuestion[];
  backHref: Route;
  backLabel?: string;
}

/**
 * Self-marked practice for a structured paper.
 *
 * There is no option to click and no answer to check automatically — a
 * structured question is answered on paper, or in the student's head, and
 * the only thing this can do is show the mark scheme once they are ready and
 * ask them to say honestly how many of each part's marks they earned. That
 * self-report is the whole product here: not a score the app computed, a
 * record of what the student was willing to admit against the actual mark
 * scheme, which is a more useful number than a green tick could ever be.
 *
 * Untimed, unlike the mcq arena: a real attempt at a six-mark question takes
 * as long as it takes, and a clock counting down against handwriting speed
 * would be measuring the wrong thing.
 */
export function StructuredArena({ sessionKey, title, questions, backHref, backLabel = "Leave practice" }: Props) {
  const [state, setState] = useState<StructuredSessionState | null>(null);
  const [resumed, setResumed] = useState(false);
  const { user, supabase } = useAuth();
  const questionEnteredAt = useRef<number>(Date.now());
  const timeSpent = useRef<Map<string, number>>(new Map());

  const total = questions.length;

  useEffect(() => {
    const saved = loadStructuredSession(sessionKey);
    if (saved && !saved.submitted && saved.questionIds.length === total) {
      setState(saved);
      setResumed(true);
      return;
    }
    setState({
      sessionKey,
      questionIds: questions.map((q) => q.id),
      revealed: [],
      marks: {},
      currentIndex: 0,
      startedAt: Date.now(),
      submitted: false,
    });
  }, [sessionKey, questions, total]);

  useEffect(() => {
    if (state) saveStructuredSession(state);
  }, [state]);

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

  function reveal() {
    setState((prev) => {
      if (!prev || !current || prev.revealed.includes(current.id)) return prev;
      return { ...prev, revealed: [...prev.revealed, current.id] };
    });
  }

  function setMark(partLabel: string, value: number) {
    setState((prev) => {
      if (!prev || !current) return prev;
      return {
        ...prev,
        marks: {
          ...prev.marks,
          [current.id]: { ...prev.marks[current.id], [partLabel]: value },
        },
      };
    });
  }

  // Every part that has a mark scheme to check against — a part the mark
  // scheme never matched (see the review queue's unmatched_mark_scheme) has
  // no maxMarks to self-mark out of, and is shown as a caveat instead.
  const markable = useCallback(
    (question: StructuredQuestion) => question.parts.filter((p) => p.maxMarks != null),
    [],
  );

  const submit = useCallback(() => {
    setState((prev) => {
      if (!prev || prev.submitted) return prev;
      const leaving = questions[prev.currentIndex];
      if (leaving) chargeTime(leaving.id);

      const records = questions
        .filter((q) => prev.marks[q.id])
        .map((q) => {
          const given = prev.marks[q.id] ?? {};
          const parts = markable(q).filter((p) => given[p.displayLabel] != null);
          return {
            questionId: q.id,
            paperSlug: q.paperSlug,
            marksAwarded: parts.reduce((sum, p) => sum + (given[p.displayLabel] ?? 0), 0),
            maxMarks: parts.reduce((sum, p) => sum + (p.maxMarks ?? 0), 0),
            timeSpentMs: timeSpent.current.get(q.id) ?? 0,
            createdAt: Date.now(),
          };
        });

      appendStructuredAttempts(records);
      if (user && supabase) void syncStructuredAttemptsToServer(supabase, user.id, records);

      return { ...prev, submitted: true };
    });
  }, [chargeTime, questions, markable, user, supabase]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!state || state.submitted || event.metaKey || event.ctrlKey) return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName)) return;
      if (event.key === "ArrowRight") goTo(state.currentIndex + 1);
      if (event.key === "ArrowLeft") goTo(state.currentIndex - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!state || !current) {
    return <div className="py-20 text-center text-ink-3">Loading…</div>;
  }

  const isRevealed = state.revealed.includes(current.id);
  const currentMarks = state.marks[current.id] ?? {};
  const currentParts = markable(current);
  const markedCount = currentParts.filter((p) => currentMarks[p.displayLabel] != null).length;

  const doneCount = questions.filter((q) => {
    const parts = markable(q);
    const given = state.marks[q.id] ?? {};
    return parts.length > 0 && parts.every((p) => given[p.displayLabel] != null);
  }).length;

  if (state.submitted) {
    return (
      <StructuredResults
        backHref={backHref}
        title={title}
        questions={questions}
        marks={state.marks}
        markable={markable}
        timeSpent={timeSpent.current}
        onRetake={() => {
          clearStructuredSession(sessionKey);
          timeSpent.current = new Map();
          setState({
            sessionKey,
            questionIds: questions.map((q) => q.id),
            revealed: [],
            marks: {},
            currentIndex: 0,
            startedAt: Date.now(),
            submitted: false,
          });
        }}
      />
    );
  }

  return (
    <div>
      {resumed && (
        <div className="mb-4 rounded-xl border border-line bg-marks-soft px-4 py-2.5 text-sm text-ink-2">
          Resumed where you left off — {doneCount} of {total} self-marked.
        </div>
      )}

      <div className="mb-5 flex flex-wrap items-center gap-4">
        <span className="text-sm text-ink-2">{doneCount} of {total} self-marked</span>
        <button
          type="button"
          onClick={submit}
          className="ml-auto rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
        >
          Finish
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_200px]">
        <div className="rounded-2xl border border-line bg-surface p-6 shadow-card">
          <div className="mb-4 flex items-center justify-between">
            <span className="font-mono text-sm text-ink-3">
              Question {current.displayLabel} · {state.currentIndex + 1} / {total}
            </span>
            {currentParts.length > 0 && (
              <span className="text-xs text-ink-3">
                {currentParts.reduce((sum, p) => sum + (p.maxMarks ?? 0), 0)} marks
              </span>
            )}
          </div>

          {current.crops.length ? (
            <div className="space-y-2">
              {current.crops.map((crop) => (
                <div
                  key={crop.pageNumber}
                  className="overflow-hidden rounded-xl border border-line bg-white"
                >
                  {crop.cropUrl ? (
                    /* eslint-disable-next-line @next/next/no-img-element -- a stable
                       /api/asset path, resolved per request. */
                    <img
                      src={crop.cropUrl}
                      alt={`Question ${current.displayLabel}, page ${crop.pageNumber}, as printed`}
                      className="w-full"
                    />
                  ) : (
                    <p className="p-6 text-center text-xs text-ink-3">
                      Crop not available for page {crop.pageNumber}.
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="rounded-xl border border-dashed border-line bg-surface-2 p-6 text-center text-sm text-ink-3">
              No crop available for this question.
            </p>
          )}

          {!isRevealed ? (
            <div className="mt-6 flex flex-col items-center gap-3 rounded-xl border border-dashed border-line bg-surface-2 p-6 text-center">
              <p className="max-w-sm text-sm leading-relaxed text-ink-2">
                Work through {current.displayLabel} on paper, then reveal the mark
                scheme and mark yourself honestly, part by part.
              </p>
              <button
                type="button"
                onClick={reveal}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
              >
                Show mark scheme
              </button>
            </div>
          ) : (
            <div className="mt-6 border-t border-line pt-5">
              <div className="mb-3 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
                  Mark yourself
                </p>
                {currentParts.length > 0 && (
                  <span className="font-mono text-xs text-ink-3">
                    {markedCount}/{currentParts.length} parts marked
                  </span>
                )}
              </div>

              <div className="space-y-4">
                {current.parts.map((part) => (
                  <PartMarker
                    key={part.displayLabel}
                    part={part}
                    given={currentMarks[part.displayLabel] ?? null}
                    onMark={(value) => setMark(part.displayLabel, value)}
                  />
                ))}
              </div>
            </div>
          )}

          <div className="mt-6 flex items-center justify-between border-t border-line pt-4">
            <button
              type="button"
              onClick={() => goTo(state.currentIndex - 1)}
              disabled={state.currentIndex === 0}
              className="rounded-lg px-3 py-1.5 text-sm text-ink-2 transition-colors hover:bg-surface-2 disabled:opacity-40"
            >
              ← Previous
            </button>
            <span className="hidden text-xs text-ink-3 sm:inline">
              <kbd className="rounded border border-line px-1">←→</kbd> navigate
            </span>
            <button
              type="button"
              onClick={() => goTo(state.currentIndex + 1)}
              disabled={state.currentIndex === total - 1}
              className="rounded-lg px-3 py-1.5 text-sm text-ink-2 transition-colors hover:bg-surface-2 disabled:opacity-40"
            >
              Next →
            </button>
          </div>
        </div>

        <aside>
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-3">
            Questions
          </h2>
          <div className="grid grid-cols-8 gap-1.5 lg:grid-cols-5">
            {questions.map((question, index) => {
              const parts = markable(question);
              const given = state.marks[question.id] ?? {};
              const markedHere = parts.filter((p) => given[p.displayLabel] != null).length;
              const done = parts.length > 0 && markedHere === parts.length;
              const partial = markedHere > 0 && !done;
              const active = index === state.currentIndex;
              return (
                <button
                  key={question.id}
                  type="button"
                  onClick={() => goTo(index)}
                  aria-label={`Question ${question.displayLabel}${done ? ", self-marked" : ""}`}
                  className={`relative aspect-square rounded-md font-mono text-xs transition-colors ${
                    active
                      ? "bg-accent text-accent-ink"
                      : done
                        ? "bg-accent-soft text-ink"
                        : "bg-surface-2 text-ink-3 hover:text-ink"
                  }`}
                >
                  {question.displayLabel}
                  {partial && (
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

function PartMarker({
  part,
  given,
  onMark,
}: {
  part: StructuredPart;
  given: number | null;
  onMark: (value: number) => void;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-sm text-ink">{part.displayLabel}</span>
        {part.maxMarks != null && (
          <span className="font-mono text-xs text-ink-3">[{part.maxMarks}]</span>
        )}
      </div>
      {part.markSchemeText ? (
        <p className="mt-1 text-sm leading-relaxed text-ink-2">{part.markSchemeText}</p>
      ) : (
        <p className="mt-1 text-sm italic text-ink-3">
          No mark scheme attached to this part — use your own judgement.
        </p>
      )}
      {part.maxMarks != null && (
        <div className="mt-2 flex items-center gap-1.5" role="radiogroup" aria-label={`Marks for ${part.displayLabel}`}>
          <span className="mr-1 text-xs text-ink-3">Marks:</span>
          {Array.from({ length: part.maxMarks + 1 }, (_, value) => value).map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={given === value}
              onClick={() => onMark(value)}
              className={`flex h-7 w-7 items-center justify-center rounded-md font-mono text-xs transition-colors ${
                given === value
                  ? "bg-accent text-accent-ink"
                  : "bg-surface-2 text-ink-2 hover:bg-accent-soft"
              }`}
            >
              {value}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function StructuredResults({
  backHref,
  title,
  questions,
  marks,
  markable,
  timeSpent,
  onRetake,
}: {
  backHref: Route;
  title: string;
  questions: StructuredQuestion[];
  marks: Record<string, Record<string, number>>;
  markable: (question: StructuredQuestion) => StructuredPart[];
  timeSpent: Map<string, number>;
  onRetake: () => void;
}) {
  const totals = useMemo(() => {
    let awarded = 0;
    let markedPossible = 0;
    let totalPossible = 0;
    let markedParts = 0;
    let totalParts = 0;

    for (const question of questions) {
      const given = marks[question.id] ?? {};
      for (const part of markable(question)) {
        totalParts += 1;
        totalPossible += part.maxMarks ?? 0;
        const score = given[part.displayLabel];
        if (score != null) {
          markedParts += 1;
          markedPossible += part.maxMarks ?? 0;
          awarded += score;
        }
      }
    }
    return { awarded, markedPossible, totalPossible, markedParts, totalParts };
  }, [questions, marks, markable]);

  const percentage = totals.markedPossible
    ? Math.round((totals.awarded / totals.markedPossible) * 100)
    : 0;
  const totalTime = [...timeSpent.values()].reduce((sum, ms) => sum + ms, 0);
  const allMarked = totals.markedParts === totals.totalParts;

  return (
    <div>
      <div className="rounded-2xl border border-line bg-surface p-7 shadow-card">
        <p className="text-sm text-ink-3">{title}</p>
        <div className="mt-3 flex flex-wrap items-end gap-8">
          <div>
            <span className="font-serif text-5xl tabular-nums text-ink">
              {totals.awarded}
              <span className="text-2xl text-ink-3">/{totals.markedPossible}</span>
            </span>
            <p className="mt-1 text-sm text-ink-2">
              {totals.markedPossible ? `${percentage}% of marks self-marked` : "nothing self-marked yet"}
            </p>
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
              className="rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-surface-2"
            >
              Retake
            </button>
            <Link
              href={backHref}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90"
            >
              Done
            </Link>
          </div>
        </div>
        {!allMarked && (
          <p className="mt-4 text-xs text-ink-3">
            {totals.totalParts - totals.markedParts} of {totals.totalParts} parts across this
            paper were not self-marked ({totals.totalPossible - totals.markedPossible} marks) —
            they are not counted above.
          </p>
        )}
      </div>

      <h2 className="mb-4 mt-10 font-serif text-2xl tracking-tight text-ink">Every question</h2>
      <div className="space-y-3">
        {questions.map((question) => {
          const given = marks[question.id] ?? {};
          const parts = markable(question);
          const marked = parts.filter((p) => given[p.displayLabel] != null);
          const awarded = marked.reduce((sum, p) => sum + (given[p.displayLabel] ?? 0), 0);
          const possible = marked.reduce((sum, p) => sum + (p.maxMarks ?? 0), 0);

          return (
            <div
              key={question.id}
              className="rounded-2xl border border-line bg-surface p-5 shadow-card"
            >
              <div className="flex items-start gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 font-mono text-xs text-ink-2">
                  {question.displayLabel}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    {marked.length ? (
                      <span className="text-ink">
                        {awarded}/{possible}
                      </span>
                    ) : (
                      <span className="text-ink-3">Not self-marked</span>
                    )}
                    {marked.length < parts.length && marked.length > 0 && (
                      <span className="ml-2 text-xs text-ink-3">
                        ({parts.length - marked.length} part{parts.length - marked.length === 1 ? "" : "s"} skipped)
                      </span>
                    )}
                    {timeSpent.get(question.id) ? (
                      <span className="ml-2 font-mono text-xs text-ink-3">
                        {formatDuration(timeSpent.get(question.id) ?? 0)}
                      </span>
                    ) : null}
                  </p>

                  {parts.length > 0 && (
                    <div className="mt-3 space-y-2 rounded-xl bg-surface-2 p-4">
                      {parts.map((part) => (
                        <div key={part.displayLabel} className="flex gap-3 text-sm">
                          <span className="w-20 shrink-0 font-mono text-xs text-ink-3">
                            {part.displayLabel} [{part.maxMarks}]
                          </span>
                          <p className="leading-relaxed text-ink-2">
                            {given[part.displayLabel] != null ? (
                              <>
                                <span className="font-mono text-ink">
                                  {given[part.displayLabel]}/{part.maxMarks}
                                </span>
                                {part.markSchemeText ? ` — ${part.markSchemeText}` : null}
                              </>
                            ) : (
                              <span className="italic text-ink-3">
                                Not self-marked{part.markSchemeText ? ` — ${part.markSchemeText}` : ""}
                              </span>
                            )}
                          </p>
                        </div>
                      ))}
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
