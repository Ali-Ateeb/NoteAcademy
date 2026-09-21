"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  currentDecisions,
  recordDecision,
  syncDecision,
  syncRetag,
  syncUndo,
  undoLastDecision,
} from "@/lib/review";
import {
  REVIEW_FLAG_HINTS,
  REVIEW_FLAG_LABELS,
  type DecidedItem,
  type ReviewCrop,
  type ReviewDecision,
  type ReviewFlag,
  type ReviewItem,
} from "@/lib/data/types";

/** How sure the classifier was about this question's topic.
 *
 *  Not `extractionConfidence`, which is 1.0 on every multiple-choice question
 *  the geometric segmenter accepted — a constant, and so useless both as a
 *  badge and as a sort key. */
function tagConfidence(item: ReviewItem): number {
  return item.proposedTopics[0]?.confidence ?? 0;
}

/** "page 12" for one crop, "pages 12–14" for a question that runs across
 *  several — a structured question routinely does, where an mcq never has. */
function pageLabel(crops: ReviewCrop[]): string {
  if (crops.length === 0) return "no crop";
  const pages = crops.map((crop) => crop.pageNumber);
  const min = Math.min(...pages);
  const max = Math.max(...pages);
  return min === max ? `page ${min}` : `pages ${min}–${max}`;
}

export interface TopicGroup {
  subjectSlug: string;
  subjectTitle: string;
  topics: { code: string; title: string }[];
}

/** The retag panel below only ever has a typed-in paper slug or a
 *  `DecidedItem` to work from — neither carries a real `subjectSlug` — so it
 *  still has to guess one from the paper slug's own naming convention
 *  (`${subjectSlug}-${year}-${season}-p...`). Falling back to every topic,
 *  grouped, is safer than guessing wrong if that convention ever changes. */
function matchingTopicGroups(groups: TopicGroup[], paperSlug: string): TopicGroup[] {
  const match = groups.find((g) => paperSlug.startsWith(`${g.subjectSlug}-`));
  return match ? [match] : groups;
}

/** The exact version of the same lookup, for a live `ReviewItem`: it carries
 *  `subjectSlug` straight from the database (0028), so there is nothing to
 *  guess. Used to scope the "Change to" dropdown on the card actually being
 *  reviewed to the question's own subject. */
function topicsForSubject(
  groups: TopicGroup[],
  subjectSlug: string,
): { code: string; title: string }[] {
  return (groups.find((g) => g.subjectSlug === subjectSlug) ?? { topics: [] }).topics;
}

export interface QueueFilter {
  subjectSlug: string | null;
  flag: ReviewFlag | null;
}

interface Props {
  /** The first page, rendered server-side for a fast first paint. Paging
   *  and filtering past this happen client-side against /api/review-queue —
   *  see `fetchPage` below — so the keyboard-driven reviewing flow this
   *  component is built around never stalls on a full page navigation. */
  initialItems: ReviewItem[];
  /** Rows matching no filter, across the whole queue — what "N pending" and
   *  "is there more to load" are computed against, not `initialItems.length`. */
  initialTotal: number;
  subjects: { slug: string; title: string }[];
  topicGroups: TopicGroup[];
  /** Questions already approved or rejected, newest first — how a reviewer
   *  finds one again once it has left the queue above. */
  decided: DecidedItem[];
  /** Is there a database behind this queue? With one, a decision has to reach
   *  it to count. Without one, the queue is a scaffold on fixtures and the
   *  browser is the only place a decision was ever going to live. */
  persist: boolean;
  /** The signed-in reviewer, for display only — reaching this component at
   *  all already means the server-side gate in page.tsx (`reviewerStatus()`)
   *  passed, so there is nothing left to unlock here. Null in fixtures mode,
   *  where `persist` is false and nothing is written anywhere but this
   *  browser regardless of who is signed in. */
  reviewerEmail: string | null;
}

/**
 * The review queue.
 *
 * Built around throughput. A real backfill puts thousands of questions through
 * here, so the reviewer never touches the mouse: J/K to move, A to approve, R to
 * reject, U to undo. Every decision is one keystroke, and the item advances
 * automatically. Reassigning the topic is the one action that is not: with a
 * real syllabus of sixty-three revisable nodes there is no useful mapping from
 * a digit to a topic, so it is a select.
 *
 * The queue is ordered worst-confidence first, so attention goes where the
 * pipeline is least sure rather than in page order.
 *
 * Only the first page loads up front; the subject and flag filters and the
 * automatic "load more" once the loaded buffer runs low (see `fetchPage`)
 * are what let a ten-subject backlog stay pageable instead of shipping the
 * whole queue — thousands of rows, every one of them crop-signed — on
 * first paint (see 0028_review_queue_pagination.sql).
 */
export function ReviewQueue({
  initialItems,
  initialTotal,
  subjects,
  topicGroups,
  decided,
  persist,
  reviewerEmail,
}: Props) {
  const [decisions, setDecisions] = useState<Map<string, ReviewDecision> | null>(null);
  const [index, setIndex] = useState(0);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // items/total start from the server-rendered first page and grow (or get
  // replaced, on a filter change) from /api/review-queue — see fetchPage.
  const [items, setItems] = useState<ReviewItem[]>(initialItems);
  const [total, setTotal] = useState(initialTotal);
  const [filter, setFilter] = useState<QueueFilter>({ subjectSlug: null, flag: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    const stored = currentDecisions();
    setDecisions(new Map([...stored].map(([id, r]) => [id, r.decision])));
  }, []);

  // Nothing to save to, so nothing to warn about or roll back.
  const savesToDatabase = persist;

  /** One page of /api/review-queue under the given filter. `append` grows
   *  `items` (the "load more" path); its absence replaces them (a fresh
   *  filter, or the very first load already covered by `initialItems`). */
  const fetchPage = useCallback(
    async (nextFilter: QueueFilter, offset: number, append: boolean) => {
      setLoadingMore(true);
      setLoadError(null);
      try {
        const params = new URLSearchParams({ offset: String(offset) });
        if (nextFilter.subjectSlug) params.set("subject", nextFilter.subjectSlug);
        if (nextFilter.flag) params.set("flag", nextFilter.flag);

        const response = await fetch(`/api/review-queue?${params}`);
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as { error?: string };
          setLoadError(body.error ?? `Loading more of the queue failed (${response.status}).`);
          return;
        }
        const page = (await response.json()) as { items: ReviewItem[]; total: number };
        setItems((prev) => (append ? [...prev, ...page.items] : page.items));
        setTotal(page.total);
      } catch (error) {
        setLoadError(`Loading more of the queue failed: ${(error as Error).message}`);
      } finally {
        setLoadingMore(false);
      }
    },
    [],
  );

  function applyFilter(next: QueueFilter) {
    setFilter(next);
    setIndex(0);
    void fetchPage(next, 0, false);
  }

  const pending = useMemo(
    () => (decisions ? items.filter((item) => !decisions.has(item.id)) : []),
    [items, decisions],
  );

  // Not items.length: most of a large, filtered queue is never loaded at
  // once (that is the entire point — see 0028 and getReviewQueue's own
  // docstring), so the "N pending" stat is computed against the server's
  // count instead, adjusted for whatever this session has already decided
  // among the rows it *has* loaded.
  const pendingCount = decisions
    ? total - items.filter((item) => decisions.has(item.id)).length
    : total;

  // The buffer of loaded-but-undecided questions is what the keyboard flow
  // actually consumes; once it runs low and the server says more exist under
  // the current filter, fetch the next page automatically. This is what
  // keeps "the reviewer never touches the mouse" true past the first
  // REVIEW_PAGE_SIZE questions instead of stopping there.
  useEffect(() => {
    if (showAll || loadingMore) return;
    if (pending.length > 3) return;
    if (items.length >= total) return;
    void fetchPage(filter, items.length, true);
  }, [showAll, loadingMore, pending.length, items.length, total, filter, fetchPage]);

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

    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide, undo, visible.length, current]);

  if (decisions === null) {
    return <div className="py-20 text-center text-ink-3">Loading queue…</div>;
  }

  const approved = [...decisions.values()].filter((d) => d === "approved").length;
  const rejected = [...decisions.values()].filter((d) => d === "rejected").length;

  return (
    <>
      {/* Reaching this component at all already means the server-side gate
          in page.tsx passed — reviewerEmail is who that gate found, not
          something still to unlock. Shown so a decision is never made
          without knowing which account it will be attributed to. */}
      {savesToDatabase && reviewerEmail && (
        <p className="mt-4 text-sm text-ink-3">
          Reviewing as <span className="font-medium text-ink-2">{reviewerEmail}</span>.
        </p>
      )}

      {saveError && (
        <p className="mt-4 rounded-xl border border-incorrect/40 bg-incorrect/5 px-4 py-3 text-sm text-incorrect">
          {saveError} The decision was rolled back — nothing was saved.
        </p>
      )}

      {savesToDatabase && <RetagPanel topicGroups={topicGroups} decided={decided} />}

      {/* Scopes the whole queue below to one subject and/or one flag —
          the difference between paging through a ten-subject backlog and
          paging through the few hundred rows one subject actually has. Both
          go through fetchPage, the same client-side path "load more" uses,
          so switching filters never costs a full page navigation. */}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <select
          value={filter.subjectSlug ?? ""}
          onChange={(e) =>
            applyFilter({ ...filter, subjectSlug: e.target.value || null })
          }
          className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink-2"
        >
          <option value="">All subjects</option>
          {subjects.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.title}
            </option>
          ))}
        </select>
        <select
          value={filter.flag ?? ""}
          onChange={(e) =>
            applyFilter({ ...filter, flag: (e.target.value || null) as ReviewFlag | null })
          }
          className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink-2"
        >
          <option value="">Any flag</option>
          {(Object.entries(REVIEW_FLAG_LABELS) as [ReviewFlag, string][]).map(
            ([flag, label]) => (
              <option key={flag} value={flag}>
                {label}
              </option>
            ),
          )}
        </select>
        {loadingMore && <span className="text-xs text-ink-3">Loading…</span>}
      </div>

      {loadError && (
        <p className="mt-3 rounded-xl border border-incorrect/40 bg-incorrect/5 px-4 py-3 text-sm text-incorrect">
          {loadError}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Stat label="Pending" value={String(pendingCount)} />
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
        <div className="mt-8 rounded-2xl border-2 border-dashed border-line-strong bg-surface/70 p-12 text-center">
          <p className="font-serif text-2xl text-ink">
            {filter.subjectSlug || filter.flag ? "Nothing pending for this filter." : "Queue clear."}
          </p>
          <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-3">
            {filter.subjectSlug || filter.flag
              ? "Try a broader filter, or check back as the pipeline ingests more papers."
              : "Every extracted question has been reviewed. New ones appear here as the pipeline ingests more papers."}
          </p>
        </div>
      ) : (
        <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_260px]">
          <ReviewCard
            item={current}
            topicOptions={topicsForSubject(topicGroups, current.subjectSlug)}
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
                        tagConfidence(item) < 0.75 ? "text-marks" : "text-ink-3"
                      }`}
                    >
                      {Math.round(tagConfidence(item) * 100)}%
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
    <div className="rounded-2xl border border-line-strong bg-surface p-6 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-ink">
          Q{item.displayLabel}
        </span>
        <span className="text-xs text-ink-3">
          {item.paperTitle} · {pageLabel(item.crops)}
        </span>
        {/* The *topic* confidence, not the extraction confidence.
            extraction_confidence is 1.0 on every geometrically segmented
            question — the segmenter either matched the template or the paper
            was refused — so a badge showing it read "100% confident" on all
            1238, including the ones flagged as unsure of the topic. The number
            that varies, and the only one a reviewer can act on, is how sure
            the classifier was about the topic. */}
        <span
          className={`ml-auto rounded-md px-2 py-0.5 font-mono text-xs ${
            tagConfidence(item) < 0.75
              ? "bg-marks/15 text-marks"
              : "bg-surface-2 text-ink-2"
          }`}
        >
          {item.proposedTopics.length
            ? `topic ${Math.round(tagConfidence(item) * 100)}%`
            : "untagged"}
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

      {item.crops.length ? (
        /* The question as printed. This is what the reviewer is actually
           judging — the extracted text is checked against it, and for a
           multiple-choice question whose options are diagrams it is the only
           faithful rendering there is. White background regardless of theme:
           it is a photograph of a page, not part of the interface.
           A structured question can run across a page break, in which case
           this is more than one image — stacked in reading order, each still
           the whole width of the card, rather than a carousel that hides how
           long the question actually is. */
        <div className="mt-5 space-y-2">
          {item.crops.map((crop) => (
            <div
              key={crop.storageKey}
              className="overflow-hidden rounded-xl border border-line bg-white"
            >
              {crop.url ? (
                /* eslint-disable-next-line @next/next/no-img-element -- a signed URL
                   on a bucket host, resolved per request; next/image would need the
                   host allow-listed and would proxy every crop for no benefit. */
                <img
                  src={crop.url}
                  alt={`Question ${item.displayLabel} of ${item.paperTitle}, page ${crop.pageNumber}, as printed`}
                  className="w-full"
                  loading="lazy"
                />
              ) : (
                <p className="p-4 text-center text-xs text-ink-3">
                  Signed URL missing for page {crop.pageNumber} —{" "}
                  <span className="font-mono">{crop.storageKey}</span>
                </p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-5 flex min-h-[120px] items-center justify-center rounded-xl border border-dashed border-line bg-surface-2 p-5 text-center">
          <p className="text-xs leading-relaxed text-ink-3">
            No crop to show — not generated
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

      {item.parts && item.parts.length > 0 ? (
        /* A structured question's marks and mark scheme text live on its
           leaves, not on the top-level row this card is for — "9" itself is
           never short an answer key, because it was never meant to have one.
           One row per leaf, in paper order, is what a reviewer actually
           checks the crop against: does 9(a)(ii) on the page match what is
           printed here for 9(a)(ii).

           `item.parts` is `[]`, not null, for a structured question
           segmentation found no lettered sub-parts for — genuinely one flat
           question, not a broken split. `[]` is truthy, so this used to
           render the (empty) parts branch instead of falling through to the
           question's own mark scheme below, which six approved questions
           actually have. */
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
            Mark scheme · {item.parts.length} part{item.parts.length === 1 ? "" : "s"}
          </p>
          <div className="mt-1.5 space-y-2.5">
            {item.parts.map((part) => (
              <div key={part.displayLabel} className="flex gap-3 text-sm">
                <span className="w-20 shrink-0 font-mono text-xs text-ink-3">
                  {part.displayLabel}
                  {part.maxMarks != null && (
                    <span className="ml-1">[{part.maxMarks}]</span>
                  )}
                </span>
                {part.markSchemeText ? (
                  <p className="leading-relaxed text-ink-2">{part.markSchemeText}</p>
                ) : (
                  <p className="italic text-incorrect">
                    No mark scheme matched
                    {part.reviewFlags.includes("unmatched_mark_scheme")
                      ? " — check the mark scheme's own text for this label before rejecting."
                      : "."}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
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
      )}

      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-widest text-ink-3">
          Topic
        </p>
        {/* What was actually assigned, stated plainly. This is the claim the
            reviewer is being asked to accept or correct, and it used to appear
            only as one highlighted entry among sixty-three buttons, below the
            fold. The classifier's reasoning is not stored, so there is no line
            for it — an empty "Classifier:" label said less than nothing. */}
        {primary ? (
          <p className="mt-1.5 text-sm text-ink">
            <span className="mr-2 font-mono text-xs text-ink-3">{primary.code}</span>
            {topicOptions.find((t) => t.code === primary.code)?.title ?? primary.code}
            <span
              className={`ml-2 font-mono text-xs ${
                primary.confidence < 0.75 ? "text-marks" : "text-ink-3"
              }`}
            >
              {Math.round(primary.confidence * 100)}% confident
            </span>
          </p>
        ) : (
          <p className="mt-1.5 text-sm italic text-ink-3">
            No topic assigned. Pick the one it tests.
          </p>
        )}

        {/* A select rather than a button per topic: a real CAIE tree is
            sixty-three revisable nodes, and sixty-three buttons is a wall the
            proposed one hides in. It also made the 1-9 shortcuts arbitrary —
            they reached the first nine topics in syllabus order, which has
            nothing to do with the question on screen. */}
        <label className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-3">
          <span>Change to</span>
          <select
            value={chosen ?? ""}
            onChange={(event) => onOverride(event.target.value)}
            className="max-w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink"
          >
            <option value="" disabled>
              Choose a topic…
            </option>
            {topicOptions.map((option) => (
              <option key={option.code} value={option.code}>
                {option.code} — {option.title}
              </option>
            ))}
          </select>
          {chosen && primary && chosen !== primary.code && (
            <span className="text-marks">changed from {primary.code}</span>
          )}
        </label>
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
          {item.parts
            ? `Approving publishes this and all ${item.parts.length} part${item.parts.length === 1 ? "" : "s"} to students.`
            : "Approving publishes this to students."}
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
/** Fix a question's topic after it has already left the queue — approved and
 *  published, most often, once a reviewer notices the tag was wrong. The
 *  queue itself only ever shows undecided items — approving one removes it
 *  from `items` for good, so it does not "come back" on a reload no matter
 *  how the queue is filtered — so this looks it up separately, either typed
 *  in directly or picked from the list of what was recently decided. */
function RetagPanel({
  topicGroups,
  decided,
}: {
  topicGroups: TopicGroup[];
  decided: DecidedItem[];
}) {
  const [open, setOpen] = useState(false);
  const [paperSlug, setPaperSlug] = useState("");
  const [displayLabel, setDisplayLabel] = useState("");
  const [topicCode, setTopicCode] = useState(topicGroups[0]?.topics[0]?.code ?? "");
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState<
    { kind: "saving" } | { kind: "done" } | { kind: "error"; message: string } | null
  >(null);

  // Narrows to the paper's own subject once the slug matches one — typed
  // freehand or filled in by pick() below — and shows every subject,
  // grouped, until then. Never a case where the wrong subject's topics are
  // the only ones on offer.
  const visibleGroups = matchingTopicGroups(topicGroups, paperSlug);

  const needle = filter.trim().toLowerCase();
  const filtered = needle
    ? decided.filter(
        (item) =>
          item.paperSlug.toLowerCase().includes(needle) ||
          item.displayLabel.toLowerCase().includes(needle) ||
          item.primaryTopic?.code.toLowerCase().includes(needle) ||
          item.primaryTopic?.title.toLowerCase().includes(needle),
      )
    : decided;

  function pick(item: DecidedItem) {
    setPaperSlug(item.paperSlug);
    setDisplayLabel(item.displayLabel);
    setTopicCode(
      item.primaryTopic?.code
        ?? matchingTopicGroups(topicGroups, item.paperSlug)[0]?.topics[0]?.code
        ?? "",
    );
    setStatus(null);
  }

  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="mt-6 rounded-xl border border-line bg-surface-2 px-4 py-3"
    >
      <summary className="cursor-pointer text-sm font-medium text-ink">
        Fix a question&apos;s topic after the fact
      </summary>
      <p className="mt-2 max-w-2xl text-xs leading-relaxed text-ink-3">
        For a question already approved or rejected — the queue above only shows
        what is still pending, and stops listing a question the moment it is
        decided. This changes the topic tag only; it does not touch whether the
        question is published.
      </p>

      <div className="mt-4 grid gap-4 md:grid-cols-[minmax(0,1fr)_280px]">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setStatus({ kind: "saving" });
            void syncRetag(paperSlug.trim(), displayLabel.trim(), topicCode).then(
              (result) => {
                setStatus(
                  result.ok
                    ? { kind: "done" }
                    : { kind: "error", message: result.message ?? "Save failed." },
                );
              },
            );
          }}
          className="flex flex-wrap items-end gap-3"
        >
          <label className="flex flex-col gap-1 text-xs text-ink-3">
            Paper slug
            <input
              value={paperSlug}
              onChange={(event) => setPaperSlug(event.target.value)}
              placeholder="physics-5054-2019-may-june-p11"
              required
              className="w-64 rounded-lg border border-line bg-surface px-2.5 py-1.5 font-mono text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-3">
            Question
            <input
              value={displayLabel}
              onChange={(event) => setDisplayLabel(event.target.value)}
              placeholder="11"
              required
              className="w-20 rounded-lg border border-line bg-surface px-2.5 py-1.5 font-mono text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-3">
            Correct topic
            <select
              value={topicCode}
              onChange={(event) => setTopicCode(event.target.value)}
              className="max-w-xs rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm text-ink"
            >
              {visibleGroups.map((group) => (
                <optgroup key={group.subjectSlug} label={group.subjectTitle}>
                  {group.topics.map((option) => (
                    <option key={option.code} value={option.code}>
                      {option.code} · {option.title}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={status?.kind === "saving"}
            className="pill pill-solid px-4 py-2 text-sm disabled:pointer-events-none disabled:opacity-50"
          >
            {status?.kind === "saving" ? "Saving…" : "Save topic"}
          </button>
          {status?.kind === "done" && (
            <p className="text-xs text-correct">Topic updated.</p>
          )}
          {status?.kind === "error" && (
            <p className="text-xs text-incorrect">{status.message}</p>
          )}
        </form>

        <div className="min-w-0">
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Search paper, question or topic…"
            className="w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink"
          />
          <div className="mt-2 max-h-56 space-y-1 overflow-y-auto pr-1">
            {filtered.length === 0 && (
              <p className="px-1 py-2 text-xs text-ink-3">
                {decided.length === 0
                  ? "Nothing decided yet."
                  : "No match."}
              </p>
            )}
            {filtered.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => pick(item)}
                className="block w-full rounded-lg px-2 py-1.5 text-left text-xs transition-colors hover:bg-surface"
              >
                <span
                  className={
                    item.decision === "approved" ? "text-correct" : "text-incorrect"
                  }
                >
                  {item.decision === "approved" ? "✓" : "✕"}
                </span>{" "}
                <span className="font-mono text-ink-3">
                  {item.paperSlug} Q{item.displayLabel}
                </span>
                <span className="ml-1.5 text-ink-2">
                  {item.primaryTopic
                    ? `${item.primaryTopic.code} ${item.primaryTopic.title}`
                    : "untagged"}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </details>
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
