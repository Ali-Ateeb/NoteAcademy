/**
 * Reviewer decisions.
 *
 * Written to Postgres through /api/review, and mirrored locally. The local copy
 * is not a fallback for a failed write — it is the record of what this browser
 * did, which is what makes undo work and what keeps the queue responsive while
 * a decision is in flight. A write that fails says so; it does not pretend.
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

/* ---------------------------------------------------------------------------
   The server side
   --------------------------------------------------------------------------- */

export interface SyncResult {
  ok: boolean;
  /** Shown to the reviewer verbatim. A decision that did not reach the database
   *  has not happened, and saying so beats a queue that looks saved. */
  message?: string;
  /** The server rejected the *request*, not this particular decision — the
   *  reviewer's session expired, or their account lost is_reviewer, since
   *  reaching this page at all already meant both were true a moment ago. */
  unauthorised?: boolean;
}

/** No token to attach: a same-origin fetch already carries the browser's own
 *  Supabase session cookie, which is what `/api/review` actually checks
 *  (`currentReviewer()`, against `profiles.is_reviewer`) — see
 *  0029_reviewer_accounts.sql. Writing here either works because the account
 *  reading this page is a reviewer, or it does not, and either way there is
 *  nothing this function could do differently by holding a secret of its own. */
async function send(input: RequestInfo, init: RequestInit): Promise<SyncResult> {
  try {
    const response = await fetch(input, init);
    if (response.ok) return { ok: true };

    const body = (await response.json().catch(() => ({}))) as { error?: string };
    if (response.status === 401) {
      return {
        ok: false,
        unauthorised: true,
        message: "Not authorised — sign in again as a reviewer and retry.",
      };
    }
    return { ok: false, message: body.error ?? `Save failed (${response.status}).` };
  } catch (error) {
    return { ok: false, message: `Save failed: ${(error as Error).message}` };
  }
}

export function syncDecision(
  questionId: string,
  decision: ReviewDecision,
  topicCode: string | null,
): Promise<SyncResult> {
  return send("/api/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ questionId, decision, topicCode }),
  });
}

export function syncUndo(questionId: string): Promise<SyncResult> {
  return send(`/api/review?questionId=${encodeURIComponent(questionId)}`, {
    method: "DELETE",
  });
}

/** Fix a question's topic after it has already been decided — approved or
 *  rejected, not just pending. `syncDecision`'s topic override only ever
 *  fires alongside a fresh approval; this is the door for a mistake noticed
 *  later, addressed the way a reviewer actually has the question in front of
 *  them (paper and question number) rather than by an id nothing in the UI
 *  keeps once a card leaves the queue. */
export function syncRetag(
  paperSlug: string,
  displayLabel: string,
  topicCode: string,
): Promise<SyncResult> {
  return send("/api/review", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paperSlug, displayLabel, topicCode }),
  });
}
