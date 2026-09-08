"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { currentDecisions, recordDecision, undoLastDecision } from "@/lib/review";
import {
  REVIEW_FLAG_HINTS,
  REVIEW_FLAG_LABELS,
  type ReviewDecision,
  type ReviewItem,
} from "@/lib/data/types";

interface Props {
  items: ReviewItem[];
  topicOptions: { code: string; title: string }[];
}

/**
 * The review queue.
 *
 * Built around throughput. A real backfill puts thousands of questions through
 * here, so the reviewer never touches the mouse: J/K to move, A to approve, R to
 * reject, 1–9 to reassign the topic, U to undo. Every decision is one keystroke,
 * and the item advances automatically.
 *
 * The queue is ordered worst-confidence first, so attention goes where the
 * pipeline is least sure rather than in page order.
 */
export function ReviewQueue({ items, topicOptions }: Props) {
  const [decisions, setDecisions] = useState<Map<string, ReviewDecision> | null>(null);
  const [index, setIndex] = useState(0);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const stored = currentDecisions();
    setDecisions(new Map([...stored].map(([id, r]) => [id, r.decision])));
  }, []);

  const pending = useMemo(
    () => (decisions ? items.filter((item) => !decisions.has(item.id)) : []),
    [items, decisions],
  );

  const visible = showAll ? items : pending;
  const current = visible[Math.min(index, Math.max(visible.length - 1, 0))];

  const decide = useCallback(
    (decision: ReviewDecision) => {
      if (!current) return;
      recordDecision(current.id, decision, {
        topicCode: overrides[current.id] ?? null,
      });
      setDecisions((prev) => new Map(prev).set(current.id, decision));
      // Index stays put: the decided item leaves the pending list, so the next
      // one slides into place. Advancing as well would skip a question.
      setIndex((i) => (showAll ? Math.min(i + 1, items.length - 1) : i));
    },
    [current, overrides, showAll, items.length],
  );

  const undo = useCallback(() => {
    const last = undoLastDecision();
    if (!last) return;
    setDecisions((prev) => {
      const next = new Map(prev);
      next.delete(last.questionId);
      return next;
    });
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;

      const key = event.key.toLowerCase();
      if (key === "a") { event.preventDefault(); decide("approved"); }
      if (key === "r") { event.preventDefault(); decide("rejected"); }
      if (key === "u") { event.preventDefault(); undo(); }
      if (key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setIndex((i) => Math.min(i + 1, visible.length - 1));
      }
      if (key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setIndex((i) => Math.max(i - 1, 0));
      }

      const digit = Number(event.key);
      if (current && digit >= 1 && digit <= topicOptions.length) {
        event.preventDefault();
        const option = topicOptions[digit - 1];
        if (option) {
          setOverrides((prev) => ({ ...prev, [current.id]: option.code }));
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide, undo, visible.length, current, topicOptions]);

  if (decisions === null) {
    return <div className="py-20 text-center text-ink-3">Loading queue…</div>;
  }

  const approved = [...decisions.values()].filter((d) => d === "approved").length;
  const rejected = [...decisions.values()].filter((d) => d === "rejected").length;

  return (
    <>
      <div className="mt-8 flex flex-wrap items-center gap-3">
        <Stat label="Pending" value={String(pending.length)} />
        <Stat label="Approved" value={String(approved)} tone="correct" />
        <Stat label="Rejected" value={String(rejected)} tone="incorrect" />
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => { setShowAll((v) => !v); setIndex(0); }}
            className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
          >
            {showAll ? "Show pending only" : "Show all"}
          </button>
          <button
            type="button"
            onClick={undo}
            disabled={approved + rejected === 0}
            className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:opacity-40"
          >
            Undo
          </button>
        </div>
      </div>

      {!current ? (
        <div className="mt-8 rounded-2xl border border-dashed border-line p-12 text-center">
          <p className="font-serif text-2xl text-ink">Queue clear.</p>
          <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-3">
            Every extracted question has been reviewed. New ones appear here as
            the pipeline ingests more papers.
          </p>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_260px]">
          <ReviewCard
            item={current}
            topicOptions={topicOptions}
            override={overrides[current.id] ?? null}
            decision={decisions.get(current.id) ?? null}
            onOverride={(code) =>
              setOverrides((prev) => ({ ...prev, [current.id]: code }))
            }
            onDecide={decide}
          />

          <aside>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-3">
              Queue · worst first
            </h2>
            <div className="space-y-1">
              {visible.map((item, i) => {
                const itemDecision = decisions.get(item.id);
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setIndex(i)}
                    className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors ${
                      item.id === current.id
                        ? "bg-accent-soft text-ink"
                        : "text-ink-2 hover:bg-surface-2"
                    }`}
                  >
                    <span className="font-mono text-xs text-ink-3">
                      Q{item.displayLabel}
                    </span>
                    <span
                      className={`ml-auto font-mono text-xs ${
                        item.extractionConfidence < 0.6 ? "text-incorrect" : "text-ink-3"
                      }`}
                    >
                      {Math.round(item.extractionConfidence * 100)}%
                    </span>
                    {itemDecision && (
                      <span
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                          itemDecision === "approved" ? "bg-correct" : "bg-incorrect"
                        }`}
                      />
                    )}
                  </button>
                );
              })}
            </div>

            <div className="mt-5 rounded-xl border border-line bg-surface p-3 text-xs leading-relaxed text-ink-3">
              <p className="mb-1.5 font-semibold uppercase tracking-widest">Keys</p>
              <p>
                <kbd className="rounded border border-line px-1">A</kbd> approve ·{" "}
                <kbd className="rounded border border-line px-1">R</kbd> reject
              </p>
              <p className="mt-1">
                <kbd className="rounded border border-line px-1">J</kbd>/
                <kbd className="rounded border border-line px-1">K</kbd> move ·{" "}
                <kbd className="rounded border border-line px-1">U</kbd> undo
              </p>
              <p className="mt-1">
                <kbd className="rounded border border-line px-1">1</kbd>–
                <kbd className="rounded border border-line px-1">9</kbd> set topic
              </p>
            </div>
          </aside>
        </div>
      )}
    </>
  );
}

function ReviewCard({
  item,
  topicOptions,
  override,
  decision,
  onOverride,
  onDecide,
}: {
  item: ReviewItem;
  topicOptions: { code: string; title: string }[];
  override: string | null;
  decision: ReviewDecision | null;
  onOverride: (code: string) => void;
  onDecide: (decision: ReviewDecision) => void;
}) {
  const primary = item.proposedTopics[0];
  const chosen = override ?? primary?.code ?? null;

  return (
    <div className="rounded-2xl border border-line bg-surface p-6 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-ink">
          Q{item.displayLabel}
        </span>
        <span className="text-xs text-ink-3">
          {item.paperTitle} · page {item.pageNumber}
        </span>
        <span
          className={`ml-auto rounded-md px-2 py-0.5 font-mono text-xs ${
            item.extractionConfidence < 0.6
              ? "bg-incorrect-soft text-incorrect"
              : "bg-surface-2 text-ink-2"
          }`}
        >
          {Math.round(item.extractionConfidence * 100)}% confident
        </span>
        {decision && (
          <span
            className={`rounded-md px-2 py-0.5 text-xs font-medium ${
              decision === "approved"
                ? "bg-correct-soft text-correct"
                : "bg-incorrect-soft text-incorrect"
            }`}
          >
            {decision}
          </span>
        )}
      </div>

      {/* Why this was held back. Naming the specific check that fired is what
          makes a queue of thousands tractable — the reviewer knows where to
          look instead of re-reading everything. */}
      <div className="mt-4 space-y-2">
        {item.flags.map((flag) => (
          <div key={flag} className="rounded-xl bg-marks-soft p-3">
            <p className="text-xs font-semibold uppercase tracking-widest text-marks">
              {REVIEW_FLAG_LABELS[flag]}
            </p>
            <p className="mt-1 text-sm leading-relaxed text-ink-2">
              {REVIEW_FLAG_HINTS[flag]}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-5 flex min-h-[120px] items-center justify-center rounded-xl border border-dashed border-line bg-surface-2 p-5 text-center">
        <p className="text-xs leading-relaxed text-ink-3">
          Rendered crop
          <br />
          <span className="font-mono">{item.cropStorageKey ?? "not generated"}</span>
          <br />
          <span className="mt-1 inline-block">
            The reviewer checks the extracted text against this image, which is
            why the crop is stored rather than only the text.
          </span>
        </p>
      </div>

      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
          Extracted question
        </p>
        {item.questionText ? (
          <p className="mt-1.5 text-sm leading-relaxed text-ink">{item.questionText}</p>
        ) : (
          /* Not a blank question: multiple-choice papers are ingested
             geometrically, and their text has not been read yet. Saying so
             stops a reviewer reading an empty box as a broken extraction. */
          <p className="mt-1.5 text-sm italic leading-relaxed text-ink-3">
            No text extracted yet — this question was segmented from the page,
            not read. Check it against the crop.
          </p>
        )}
      </div>

      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
          {item.questionType === "mcq" ? "Answer key" : "Mark scheme"}
        </p>
        {/* A multiple-choice mark scheme is an answer grid, so the answer is
            the mark scheme. Warning that none is attached when the key is
            recorded is a false alarm on every MCQ in the bank, and false
            alarms are how a reviewer learns to stop reading them. */}
        {item.correctOption ? (
          <p className="mt-1.5 text-sm leading-relaxed text-ink-2">
            <span className="font-mono font-semibold text-correct">
              {item.correctOption}
            </span>
            {item.markScheme ? ` — ${item.markScheme}` : " — from the mark scheme's answer grid"}
          </p>
        ) : item.markScheme ? (
          <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{item.markScheme}</p>
        ) : (
          <p className="mt-1.5 text-sm italic text-incorrect">
            None attached — approving without one leaves the question unusable in
            the arena.
          </p>
        )}
      </div>

      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
          Topic
        </p>
        {primary && (
          <p className="mt-1.5 text-xs italic leading-relaxed text-ink-3">
            Classifier: {primary.reasoning}
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-1.5">
          {topicOptions.map((option, i) => {
            const proposed = item.proposedTopics.find((t) => t.code === option.code);
            const selected = chosen === option.code;
            return (
              <button
                key={option.code}
                type="button"
                onClick={() => onOverride(option.code)}
                className={`rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
                  selected
                    ? "border-accent bg-accent-soft text-ink"
                    : "border-line text-ink-2 hover:border-line-strong"
                }`}
              >
                <span className="mr-1.5 font-mono text-ink-3">{i + 1}</span>
                {option.title}
                {proposed && (
                  <span className="ml-1.5 font-mono text-ink-3">
                    {Math.round(proposed.confidence * 100)}%
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-6 flex gap-2 border-t border-line pt-4">
        <button
          type="button"
          onClick={() => onDecide("approved")}
          className="rounded-lg bg-correct px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => onDecide("rejected")}
          className="rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-surface-2"
        >
          Reject
        </button>
        <p className="ml-auto self-center text-xs text-ink-3">
          Approving publishes this to students.
        </p>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "correct" | "incorrect";
}) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-2.5 shadow-card">
      <span
        className={`font-mono text-lg tabular-nums ${
          tone === "correct"
            ? "text-correct"
            : tone === "incorrect"
              ? "text-incorrect"
              : "text-ink"
        }`}
      >
        {value}
      </span>
      <span className="ml-2 text-xs text-ink-3">{label}</span>
    </div>
  );
}
