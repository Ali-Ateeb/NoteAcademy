/**
 * Client side of `/api/question-meta`: resolves the subject and topics of the
 * questions a student has attempted, so the dashboards never need the whole
 * question bank shipped to them.
 *
 * Three screens ask the same question of the same ids (the dashboard index,
 * a subject dashboard, the revision queue), so answers are kept for the life
 * of the page: a student moving between them pays for the lookup once, and
 * an id the server said it does not know is remembered as unknown rather
 * than asked about again.
 */

export interface QuestionMeta {
  subjectSlug: string;
  topicCodes: string[];
}

// null = asked, and the server does not know it (not an approved MCQ).
const known = new Map<string, QuestionMeta | null>();

// The route accepts far more than this; smaller requests just keep any one
// response, and any one retry, small.
const BATCH = 1000;

export async function resolveQuestionMeta(ids: readonly string[]): Promise<Map<string, QuestionMeta>> {
  const missing = [...new Set(ids)].filter((id) => !known.has(id));

  for (let i = 0; i < missing.length; i += BATCH) {
    const batch = missing.slice(i, i + BATCH);
    const res = await fetch("/api/question-meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: batch }),
    });
    if (!res.ok) throw new Error(`question lookup failed (${res.status})`);

    const body: { questions?: { id: string; subjectSlug: string; topicCodes: string[] }[] } =
      await res.json();
    for (const id of batch) known.set(id, null);
    for (const q of body.questions ?? []) {
      known.set(q.id, { subjectSlug: q.subjectSlug, topicCodes: q.topicCodes });
    }
  }

  const out = new Map<string, QuestionMeta>();
  for (const id of ids) {
    const meta = known.get(id);
    if (meta) out.set(id, meta);
  }
  return out;
}

/** The lookup narrowed to one subject, in the `questionId → topic codes` shape
 *  `computeTopicStats` takes. Codes like "1.1" repeat across subjects, so an
 *  attempt at another subject's question must never contribute to this one. */
export async function topicsByQuestionForSubject(
  subjectSlug: string,
  ids: readonly string[],
): Promise<Map<string, string[]>> {
  const meta = await resolveQuestionMeta(ids);
  const out = new Map<string, string[]>();
  for (const [id, m] of meta) {
    if (m.subjectSlug === subjectSlug) out.set(id, m.topicCodes);
  }
  return out;
}
