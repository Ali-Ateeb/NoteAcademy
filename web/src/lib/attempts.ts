/**
 * Attempt persistence.
 *
 * localStorage for now, so the arena is usable with no auth and no backend. The
 * shape mirrors the `attempts` and `practice_sessions` tables exactly, which is
 * what makes the switch to Postgres a change of transport rather than a rewrite
 * — and what lets a signed-out student's local history be migrated into their
 * account on first login.
 *
 * The log is append-only here for the same reason it is in the database: a
 * changed answer is a new entry, and "their answer" is the last one. Keeping the
 * history is what makes "you got this wrong the first two times" possible.
 */

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

/** Per-topic accuracy from the local log — the weak-area heatmap. */
export function topicStats(
  topicsByQuestion: Map<string, string[]>,
): Map<string, TopicStat> {
  const buckets = new Map<string, { correct: number; times: number[] }>();

  for (const attempt of currentAnswers().values()) {
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
