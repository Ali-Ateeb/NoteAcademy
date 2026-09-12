/**
 * Attempt persistence.
 *
 * localStorage always, so the arena is usable with no auth and no backend, and
 * stays usable offline even once signed in. The shape mirrors the `attempts`
 * and `practice_sessions` tables exactly, which is what makes writing to
 * Postgres a second transport rather than a rewrite — and what lets a
 * signed-out student's local history be migrated into their account on first
 * login (see `migrateLocalAttempts` below).
 *
 * The log is append-only here for the same reason it is in the database: a
 * changed answer is a new entry, and "their answer" is the last one. Keeping the
 * history is what makes "you got this wrong the first two times" possible.
 *
 * Signed in, every submit writes both places: local storage first — instant,
 * and the thing the arena's own UI reads back — then a best-effort mirror to
 * `attempts` (see `syncAttemptsToServer`/`syncStructuredAttemptsToServer`).
 * The dashboard is the one thing that actually switches source when signed
 * in, reading `current_answers` instead of the local log, which is what
 * makes progress visible on a second device.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export interface AttemptRecord {
  questionId: string;
  paperSlug: string;
  selectedOption: string;
  isCorrect: boolean;
  timeSpentMs: number;
  createdAt: number;
  /** Monotonic within this device. Timestamps tie when answers are submitted
   *  together, exactly as now() ties inside one database transaction. */
  seq: number;
}

export interface SessionState {
  /** Identifies the sitting: a paper slug for an exam, a topic key for a drill.
   *  Distinct keys are what stop a topic drill overwriting a half-finished
   *  paper. */
  sessionKey: string;
  questionIds: string[];
  answers: Record<string, string>;
  flagged: string[];
  currentIndex: number;
  startedAt: number;
  /** Seconds left when the tab was last open. Time does not run while away —
   *  a student who loses their connection should not lose the paper. */
  remainingSeconds: number;
  submitted: boolean;
}

const ATTEMPTS_KEY = "na-attempts";
const SESSION_PREFIX = "na-session:";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    // Private browsing, blocked site data, or corrupt JSON. Losing local
    // history is survivable; throwing inside a render is not.
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota exceeded or storage blocked — the test still works in-memory.
  }
}

export function loadAttempts(): AttemptRecord[] {
  return read<AttemptRecord[]>(ATTEMPTS_KEY, []);
}

export function appendAttempts(records: Omit<AttemptRecord, "seq">[]): void {
  const existing = loadAttempts();
  const nextSeq = existing.reduce((max, a) => Math.max(max, a.seq), 0) + 1;
  write(
    ATTEMPTS_KEY,
    existing.concat(records.map((record, i) => ({ ...record, seq: nextSeq + i }))),
  );
}

/** Latest attempt per question — the equivalent of the current_answers view. */
export function currentAnswers(): Map<string, AttemptRecord> {
  const latest = new Map<string, AttemptRecord>();
  for (const attempt of loadAttempts()) {
    const seen = latest.get(attempt.questionId);
    if (!seen || attempt.seq > seen.seq) latest.set(attempt.questionId, attempt);
  }
  return latest;
}

export function loadSession(sessionKey: string): SessionState | null {
  return read<SessionState | null>(SESSION_PREFIX + sessionKey, null);
}

export function saveSession(state: SessionState): void {
  write(SESSION_PREFIX + state.sessionKey, state);
}

export function clearSession(sessionKey: string): void {
  try {
    localStorage.removeItem(SESSION_PREFIX + sessionKey);
  } catch {
    // Nothing to do; a stale session is harmless.
  }
}

export interface TopicStat {
  attempted: number;
  correct: number;
  accuracy: number;
  medianMs: number;
}

/** Per-topic accuracy from an already-loaded set of current answers. Pulled
 *  out of `topicStats` so the identical aggregation can run over either the
 *  local log or a `current_answers` row set from the server — the two differ
 *  only in where the attempts came from, never in how they are summarised. */
export function computeTopicStats(
  attempts: Iterable<Pick<AttemptRecord, "questionId" | "isCorrect" | "timeSpentMs">>,
  topicsByQuestion: Map<string, string[]>,
): Map<string, TopicStat> {
  const buckets = new Map<string, { correct: number; times: number[] }>();

  for (const attempt of attempts) {
    for (const code of topicsByQuestion.get(attempt.questionId) ?? []) {
      const bucket = buckets.get(code) ?? { correct: 0, times: [] };
      if (attempt.isCorrect) bucket.correct += 1;
      bucket.times.push(attempt.timeSpentMs);
      buckets.set(code, bucket);
    }
  }

  const stats = new Map<string, TopicStat>();
  for (const [code, bucket] of buckets) {
    const sorted = [...bucket.times].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    stats.set(code, {
      attempted: sorted.length,
      correct: bucket.correct,
      accuracy: sorted.length ? bucket.correct / sorted.length : 0,
      medianMs:
        sorted.length === 0
          ? 0
          : sorted.length % 2
            ? (sorted[mid] ?? 0)
            : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2,
    });
  }
  return stats;
}

/** Per-topic accuracy from the local log — the weak-area heatmap when signed
 *  out. */
export function topicStats(
  topicsByQuestion: Map<string, string[]>,
): Map<string, TopicStat> {
  return computeTopicStats(currentAnswers().values(), topicsByQuestion);
}

/* ---------------------------------------------------------------------------
   Structured (Paper 2) practice
   --------------------------------------------------------------------------- */

/** A student's self-assessed score for one part, checked against the mark
 *  scheme themselves — there is no answer to grade automatically, the way an
 *  mcq's option letter can be. `marksAwarded` is what the student claims,
 *  not something the app has any way to verify; the point of the record is
 *  the same one exam self-marking always serves — an honest read of where
 *  marks are actually being lost. */
export interface StructuredAttemptRecord {
  questionId: string;
  paperSlug: string;
  marksAwarded: number;
  maxMarks: number;
  timeSpentMs: number;
  createdAt: number;
  seq: number;
}

export interface StructuredSessionState {
  sessionKey: string;
  questionIds: string[];
  /** This question's mark scheme has been shown. Gates self-marking: a mark
   *  entered before the mark scheme is seen is not a self-assessment. */
  revealed: string[];
  /** marks[questionId][partDisplayLabel] = what the student gave themself.
   *  A part missing from the inner record is not yet marked — distinct from
   *  an actual 0, which the student has to choose the same as any other
   *  value. */
  marks: Record<string, Record<string, number>>;
  currentIndex: number;
  startedAt: number;
  submitted: boolean;
}

const STRUCTURED_ATTEMPTS_KEY = "na-structured-attempts";
const STRUCTURED_SESSION_PREFIX = "na-structured-session:";

export function loadStructuredAttempts(): StructuredAttemptRecord[] {
  return read<StructuredAttemptRecord[]>(STRUCTURED_ATTEMPTS_KEY, []);
}

export function appendStructuredAttempts(
  records: Omit<StructuredAttemptRecord, "seq">[],
): void {
  const existing = loadStructuredAttempts();
  const nextSeq = existing.reduce((max, a) => Math.max(max, a.seq), 0) + 1;
  write(
    STRUCTURED_ATTEMPTS_KEY,
    existing.concat(records.map((record, i) => ({ ...record, seq: nextSeq + i }))),
  );
}

export function loadStructuredSession(sessionKey: string): StructuredSessionState | null {
  return read<StructuredSessionState | null>(STRUCTURED_SESSION_PREFIX + sessionKey, null);
}

export function saveStructuredSession(state: StructuredSessionState): void {
  write(STRUCTURED_SESSION_PREFIX + state.sessionKey, state);
}

export function clearStructuredSession(sessionKey: string): void {
  try {
    localStorage.removeItem(STRUCTURED_SESSION_PREFIX + sessionKey);
  } catch {
    // Nothing to do; a stale session is harmless.
  }
}

/* ---------------------------------------------------------------------------
   Server-side mirror
   --------------------------------------------------------------------------- */

/** One row per (browser, account): set the moment this device's local history
 *  has been pushed to the server, so a second sign-in on the same device does
 *  not insert the same attempts again. Never cleared — the point is exactly
 *  that it stays true forever once migration has happened once. */
function migratedKey(userId: string): string {
  return `na-migrated:${userId}`;
}

function hasMigrated(userId: string): boolean {
  try {
    return localStorage.getItem(migratedKey(userId)) === "true";
  } catch {
    // Can't tell — assume yes rather than risk inserting the same history on
    // every sign-in for a browser that cannot remember it already did.
    return true;
  }
}

function markMigrated(userId: string): void {
  try {
    localStorage.setItem(migratedKey(userId), "true");
  } catch {
    // Storage is blocked; migration will simply be retried next sign-in.
  }
}

/** Mirrors a batch of mcq attempts to `attempts`. Called after the local
 *  append, never instead of it — a failed write here costs a student nothing
 *  they did not already have on this device. */
export async function syncAttemptsToServer(
  supabase: SupabaseClient,
  userId: string,
  records: readonly Pick<AttemptRecord, "questionId" | "selectedOption" | "isCorrect" | "timeSpentMs">[],
): Promise<void> {
  if (records.length === 0) return;
  await supabase.from("attempts").insert(
    records.map((r) => ({
      user_id: userId,
      question_id: r.questionId,
      selected_option: r.selectedOption,
      is_correct: r.isCorrect,
      time_spent_ms: r.timeSpentMs,
    })),
  );
}

/** Same idea for structured self-marks: `marksAwarded / maxMarks` is the
 *  0–1 self_marked_score column asks for. A question with no markable parts
 *  (maxMarks 0) has nothing to record — there is no meaningful fraction of
 *  zero. */
export async function syncStructuredAttemptsToServer(
  supabase: SupabaseClient,
  userId: string,
  records: readonly Pick<StructuredAttemptRecord, "questionId" | "marksAwarded" | "maxMarks" | "timeSpentMs">[],
): Promise<void> {
  const markable = records.filter((r) => r.maxMarks > 0);
  if (markable.length === 0) return;
  await supabase.from("attempts").insert(
    markable.map((r) => ({
      user_id: userId,
      question_id: r.questionId,
      self_marked_score: r.marksAwarded / r.maxMarks,
      time_spent_ms: r.timeSpentMs,
    })),
  );
}

/** Runs once per (browser, account), triggered from `AuthProvider` on
 *  sign-in: pushes whatever this device recorded while signed out into the
 *  student's real history, rather than a new account starting from zero and
 *  quietly discarding practice that already happened. Left unmigrated on
 *  failure so the next sign-in tries again instead of losing the history for
 *  good. */
export async function migrateLocalAttempts(
  supabase: SupabaseClient,
  userId: string,
): Promise<void> {
  if (hasMigrated(userId)) return;

  try {
    await syncAttemptsToServer(supabase, userId, loadAttempts());
    await syncStructuredAttemptsToServer(supabase, userId, loadStructuredAttempts());
    markMigrated(userId);
  } catch {
    // Network error or an RLS/schema mismatch — either way, silent failure
    // here is the right default: it must never block the sign-in itself.
  }
}

/** The shape of a row read back from the `current_answers` view — the
 *  server-side counterpart to `currentAnswers()` above. */
export interface RemoteCurrentAnswer {
  question_id: string;
  is_correct: boolean | null;
  time_spent_ms: number | null;
}

/** Adapts `current_answers` rows into the shape `computeTopicStats` expects.
 *  Structured self-marks carry no `is_correct` (there is nothing to compare
 *  against) and are excluded here, matching `topicStats()`'s own local
 *  behaviour today — this is a transport change, not a change in what the
 *  dashboard measures. */
export function fromRemoteCurrentAnswers(
  rows: readonly RemoteCurrentAnswer[],
): Pick<AttemptRecord, "questionId" | "isCorrect" | "timeSpentMs">[] {
  return rows
    .filter((row): row is RemoteCurrentAnswer & { is_correct: boolean } => row.is_correct !== null)
    .map((row) => ({
      questionId: row.question_id,
      isCorrect: row.is_correct,
      timeSpentMs: row.time_spent_ms ?? 0,
    }));
}

export function formatDuration(ms: number): string {
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds);
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
