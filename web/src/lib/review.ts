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

/** The shared secret /api/review requires.
 *
 *  Kept in the browser rather than embedded in the page: a token printed into
 *  the HTML is available to everyone who can load the page, which is precisely
 *  the set of people it is supposed to exclude. Entered once, per browser. */
const TOKEN_KEY = "na-review-token";

export function reviewToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setReviewToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // A browser refusing storage cannot hold the token; writes stay local.
  }
}

/** Ask the server whether a token is accepted, before trusting it.
 *
 *  Without this, "unlocked" meant no more than "a non-empty string was typed":
 *  a wrong token hid the unlock box and then failed every save, with nothing in
 *  the UI to re-enter it through. */
export async function verifyToken(token: string): Promise<SyncResult> {
  if (!token) return { ok: false, message: "Enter the token first." };
  try {
    const response = await fetch("/api/review", {
      method: "GET",
      headers: { "x-review-token": token },
    });
    if (response.ok) return { ok: true };
    if (response.status === 401) {
      return { ok: false, message: "That token was not accepted." };
    }
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    return { ok: false, message: body.error ?? `Check failed (${response.status}).` };
  } catch (error) {
    return { ok: false, message: `Check failed: ${(error as Error).message}` };
  }
}

export interface SyncResult {
  ok: boolean;
  /** Shown to the reviewer verbatim. A decision that did not reach the database
   *  has not happened, and saying so beats a queue that looks saved. */
  message?: string;
  /** The server rejected the token itself, not this particular decision. The
   *  caller should drop it and ask for it again rather than retrying. */
  unauthorised?: boolean;
}

async function send(input: RequestInfo, init: RequestInit): Promise<SyncResult> {
  const token = reviewToken();
  if (!token) return { ok: false, message: "Not unlocked — decisions are local only." };

  try {
    const response = await fetch(input, {
      ...init,
      headers: { ...init.headers, "x-review-token": token },
    });
    if (response.ok) return { ok: true };

    const body = (await response.json().catch(() => ({}))) as { error?: string };
    if (response.status === 401) {
      return {
        ok: false,
        unauthorised: true,
        message: "That review token was not accepted.",
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
