"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  currentDecisions,
  recordDecision,
  reviewToken,
  setReviewToken,
  syncDecision,
  syncUndo,
  undoLastDecision,
  verifyToken,
} from "@/lib/review";
import {
  REVIEW_FLAG_HINTS,
  REVIEW_FLAG_LABELS,
  type ReviewDecision,
  type ReviewItem,
} from "@/lib/data/types";

interface Props {
  items: ReviewItem[];
  topicOptions: { code: string; title: string }[];
  /** Is there a database behind this queue? With one, a decision has to reach
   *  it to count. Without one, the queue is a scaffold on fixtures and the
   *  browser is the only place a decision was ever going to live. */
  persist: boolean;
  /** Is REVIEW_TOKEN set on the server? Distinguishes a browser that has not
   *  been unlocked from a server that will refuse every write regardless. */
  savingConfigured: boolean;
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
export function ReviewQueue({
  items,
  topicOptions,
  persist,
  savingConfigured,
}: Props) {
  const [decisions, setDecisions] = useState<Map<string, ReviewDecision> | null>(null);
  const [index, setIndex] = useState(0);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);
  const [unlocked, setUnlocked] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    const stored = currentDecisions();
    setDecisions(new Map([...stored].map(([id, r]) => [id, r.decision])));
    setUnlocked(Boolean(reviewToken()));
  }, []);

  // Nothing to save to, so nothing to warn about or roll back.
  const savesToDatabase = persist;

  const pending = useMemo(
    () => (decisions ? items.filter((item) => !decisions.has(item.id)) : []),
    [items, decisions],
  );

  const visible = showAll ? items : pending;
  const current = visible[Math.min(index, Math.max(visible.length - 1, 0))];

  const decide = useCallback(
    (decision: ReviewDecision) => {
      if (!current) return;
      const topicCode = overrides[current.id] ?? null;
      const questionId = current.id;

      recordDecision(questionId, decision, { topicCode });
      setDecisions((prev) => new Map(prev).set(questionId, decision));
      setSaveError(null);
      // Index stays put: the decided item leaves the pending list, so the next
      // one slides into place. Advancing as well would skip a question.
      setIndex((i) => (showAll ? Math.min(i + 1, items.length - 1) : i));

      if (!savesToDatabase) return;

      // Optimistic, then reconciled. A reviewer clearing a queue of thousands
      // cannot wait a round trip per keystroke — but a decision that never
      // reached the database has not happened, so a failure is put back on
      // screen rather than swallowed.
      void syncDecision(questionId, decision, topicCode).then((result) => {
        if (result.ok) return;
        setSaveError(result.message ?? "Save failed.");
        if (result.unauthorised) {
          // Otherwise the bar stays hidden and every later decision fails the
          // same way, with nothing on screen to re-enter the token through.
          setReviewToken("");
          setUnlocked(false);
        }
        undoLastDecision();
        setDecisions((prev) => {
          const next = new Map(prev);
          next.delete(questionId);
          return next;
        });
      });
    },
    [current, overrides, showAll, items.length, savesToDatabase],
  );

  const undo = useCallback(() => {
    const last = undoLastDecision();
    if (!last) return;
    setDecisions((prev) => {
      const next = new Map(prev);
      next.delete(last.questionId);
      return next;
    });
    setSaveError(null);
    if (!savesToDatabase) return;
    void syncUndo(last.questionId).then((result) => {
      if (result.ok) return;
      setSaveError(result.message ?? "Undo failed.");
      if (result.unauthorised) {
        setReviewToken("");
        setUnlocked(false);
      }
    });
  }, [savesToDatabase]);

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
      {/* Whether decisions are reaching the database, stated rather than
          implied. A queue that looks saved and is not is the worst outcome
          here: the work is gone and nobody knows to redo it. */}
      {savesToDatabase && !unlocked && (
        <UnlockBar
          configured={savingConfigured}
          onUnlock={(token) => {
            setReviewToken(token);
            setUnlocked(true);
            setSaveError(null);
          }}
        />
      )}

      {saveError && (
        <p className="mt-4 rounded-xl border border-incorrect/40 bg-incorrect/5 px-4 py-3 text-sm text-incorrect">
          {saveError} The decision was rolled back — nothing was saved.
        </p>
      )}

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

      {item.cropUrl ? (
        /* The question as printed. This is what the reviewer is actually
           judging — the extracted text is checked against it, and for a
           multiple-choice question whose options are diagrams it is the only
           faithful rendering there is. White background regardless of theme:
           it is a photograph of a page, not part of the interface. */
        <div className="mt-5 overflow-hidden rounded-xl border border-line bg-white">
          {/* eslint-disable-next-line @next/next/no-img-element -- a signed URL
              on a bucket host, resolved per request; next/image would need the
              host allow-listed and would proxy every crop for no benefit. */}
          <img
            src={item.cropUrl}
            alt={`Question ${item.displayLabel} of ${item.paperTitle}, as printed`}
            className="w-full"
            loading="lazy"
          />
        </div>
      ) : (
        <div className="mt-5 flex min-h-[120px] items-center justify-center rounded-xl border border-dashed border-line bg-surface-2 p-5 text-center">
          <p className="text-xs leading-relaxed text-ink-3">
            No crop to show
            <br />
            <span className="font-mono">{item.cropStorageKey ?? "not generated"}</span>
            <br />
            <span className="mt-1 inline-block">
              The reviewer checks the question against this image, so approving
              without one is signing off on something nobody has seen.
            </span>
          </p>
        </div>
      )}

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

/** Takes the shared secret /api/review requires.
 *
 *  It is entered here rather than rendered into the page because a token in the
 *  HTML is readable by everyone who can load the page — which is exactly who it
 *  is meant to keep out. This is a stopgap for a single operator; /admin needs
 *  real accounts before it is served publicly. */
function UnlockBar({
  configured,
  onUnlock,
}: {
  configured: boolean;
  onUnlock: (token: string) => void;
}) {
  const [value, setValue] = useState("");
  const [checking, setChecking] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);

  // Two different problems that used to share one message. "Enter the review
  // token (REVIEW_TOKEN in web/.env.local)" reads, to someone who has already
  // put it there, as a claim that it is missing — when what is actually being
  // asked is that this browser be given a copy.
  if (!configured) {
    return (
      <p className="mt-6 rounded-xl border border-incorrect/40 bg-incorrect/5 px-4 py-3 text-sm text-incorrect">
        Saving is switched off: <span className="font-mono">REVIEW_TOKEN</span> is
        not set in <span className="font-mono">web/.env.local</span>. Set it and
        restart the server — approvals made now stay in this browser only.
      </p>
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const token = value.trim();
        setRejected(null);
        setChecking(true);
        // Checked against the server before the box goes away. "Unlocked" used
        // to mean only "something was typed", so a wrong token hid this form
        // and then failed every save with no way back to it.
        void verifyToken(token).then((result) => {
          setChecking(false);
          if (result.ok) onUnlock(token);
          else setRejected(result.message ?? "That token was not accepted.");
        });
      }}
      className="mt-6 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface-2 px-4 py-3"
    >
      <div className="min-w-[16rem] flex-1">
        <p className="text-sm text-ink">Unlock this browser to save decisions.</p>
        <p className="mt-0.5 text-xs leading-relaxed text-ink-3">
          The server has the token; this page deliberately does not contain it,
          because anything rendered into the page is readable by everyone who can
          open the page. Paste it once and this browser will remember it — it is{" "}
          <span className="font-mono">REVIEW_TOKEN</span> in{" "}
          <span className="font-mono">web/.env.local</span>. Until then, approvals
          are remembered here and nowhere else.
        </p>
        {rejected && (
          <p className="mt-1.5 text-xs text-incorrect">{rejected}</p>
        )}
      </div>
      <input
        type="password"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Review token"
        aria-label="Review token"
        className="rounded-lg border border-line bg-surface px-3 py-1.5 font-mono text-sm text-ink"
      />
      <button
        type="submit"
        disabled={checking}
        className="rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {checking ? "Checking…" : "Unlock"}
      </button>
    </form>
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
