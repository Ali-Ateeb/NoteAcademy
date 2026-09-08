/**
 * Reviewer decisions.
 *
 * Stored locally for now, in the same shape the database expects: approving a
 * question is an update to `questions.extraction_status` plus, when the reviewer
 * changed the topic, a `question_topics` row with `source = 'human'`. When this
 * moves server-side it becomes one transaction per decision and nothing about
 * the UI changes.
 *
 * Decisions are recorded, never deleted. If a reviewer's judgement later turns
 * out to be wrong — and on a queue of thousands, some will be — you want to know
 * who approved what and when, and to be able to re-open a batch.
 */

import type { ReviewDecision } from "./data/types";

export interface DecisionRecord {
  questionId: string;
  decision: ReviewDecision;
  /** Set only when the reviewer overrode the classifier's primary topic. */
  topicCode: string | null;
  note: string | null;
  decidedAt: number;
  seq: number;
}

const KEY = "na-review-decisions";

function read(): DecisionRecord[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as DecisionRecord[]) : [];
  } catch {
    return [];
  }
}

function write(records: DecisionRecord[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(records));
  } catch {
    // Storage blocked or full. The session still works; decisions are lost on
    // reload, which is why this is a scaffold and not the production path.
  }
}

export function loadDecisions(): DecisionRecord[] {
  return read();
}

/** Latest decision per question — a reviewer may revisit one. */
export function currentDecisions(): Map<string, DecisionRecord> {
  const latest = new Map<string, DecisionRecord>();
  for (const record of read()) {
    const seen = latest.get(record.questionId);
    if (!seen || record.seq > seen.seq) latest.set(record.questionId, record);
  }
  return latest;
}

export function recordDecision(
  questionId: string,
  decision: ReviewDecision,
  options: { topicCode?: string | null; note?: string | null } = {},
): void {
  const existing = read();
  const seq = existing.reduce((max, r) => Math.max(max, r.seq), 0) + 1;
  write(
    existing.concat({
      questionId,
      decision,
      topicCode: options.topicCode ?? null,
      note: options.note ?? null,
      decidedAt: Date.now(),
      seq,
    }),
  );
}

export function undoLastDecision(): DecisionRecord | null {
  const existing = read();
  const last = existing[existing.length - 1];
  if (!last) return null;
  write(existing.slice(0, -1));
  return last;
}

export function clearDecisions(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Nothing to do.
  }
}
