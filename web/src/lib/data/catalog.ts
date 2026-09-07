/**
 * The read seam between the UI and the content store.
 *
 * Right now every function reads the seed fixtures, so the app runs with no
 * backend at all — clone, `npm run dev`, and the arena works. When the pipeline
 * has loaded real papers, these are the only functions that change: each becomes
 * a query against the schema in db/migrations, and no page component is touched.
 *
 * Keeping the boundary this narrow is deliberate. The alternative — components
 * reaching into the database directly — makes the switch a rewrite.
 */

import * as seed from "./seed";
import type { Level, McqQuestion, Paper, Subject, Topic } from "./types";

export function isBackedByDatabase(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL);
}

export async function getLevels(): Promise<Level[]> {
  return seed.levels;
}

export async function getSubjects(): Promise<Subject[]> {
  return seed.subjects;
}

export async function getSubjectsByLevel(levelCode: string): Promise<Subject[]> {
  return seed.subjects.filter((s) => s.levelCode === levelCode);
}

export async function getSubject(slug: string): Promise<Subject | null> {
  return seed.subjects.find((s) => s.slug === slug) ?? null;
}

export async function getTopics(subjectSlug: string): Promise<Topic[]> {
  return subjectSlug === "physics-5054" ? seed.topics : [];
}

export async function getTopic(
  subjectSlug: string,
  topicSlug: string,
): Promise<Topic | null> {
  const topics = await getTopics(subjectSlug);
  return topics.find((t) => t.slug === topicSlug) ?? null;
}

export async function getPapers(subjectSlug: string): Promise<Paper[]> {
  return seed.papers
    .filter((p) => p.subjectSlug === subjectSlug)
    .sort((a, b) => b.year - a.year || a.component - b.component);
}

export async function getPaper(slug: string): Promise<Paper | null> {
  return seed.papers.find((p) => p.slug === slug) ?? null;
}

/** Papers grouped by year, newest first — the shape the subject hub renders. */
export async function getPapersByYear(
  subjectSlug: string,
): Promise<{ year: number; papers: Paper[] }[]> {
  const papers = await getPapers(subjectSlug);
  const byYear = new Map<number, Paper[]>();

  for (const paper of papers) {
    const bucket = byYear.get(paper.year);
    if (bucket) bucket.push(paper);
    else byYear.set(paper.year, [paper]);
  }

  return [...byYear.entries()]
    .map(([year, group]) => ({ year, papers: group }))
    .sort((a, b) => b.year - a.year);
}

export async function getMcqQuestions(paperSlug: string): Promise<McqQuestion[]> {
  return seed.mcqQuestions.filter((q) => q.paperSlug === paperSlug);
}

/** Questions for one topic, across every paper — the topical engine. */
export async function getQuestionsByTopic(
  subjectSlug: string,
  topicCode: string,
): Promise<McqQuestion[]> {
  const papers = new Set(
    seed.papers.filter((p) => p.subjectSlug === subjectSlug).map((p) => p.slug),
  );
  return seed.mcqQuestions.filter(
    (q) => papers.has(q.paperSlug) && q.topicCodes.includes(topicCode),
  );
}

/** Papers that have MCQs loaded and are therefore playable in the arena. */
export async function getPlayablePapers(subjectSlug: string): Promise<Paper[]> {
  const papers = await getPapers(subjectSlug);
  const loaded = new Set(seed.mcqQuestions.map((q) => q.paperSlug));
  return papers.filter((p) => loaded.has(p.slug));
}

export const SEED_NOTICE = seed.SEED_NOTICE;
