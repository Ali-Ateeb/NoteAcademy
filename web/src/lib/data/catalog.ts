/**
 * The read seam between the UI and the content store.
 *
 * Two backends, one interface. With Supabase configured every function is a
 * select against the views in db/migrations/0012_read_views.sql; with nothing
 * configured they read the development fixtures, so a fresh clone still runs —
 * `npm run dev` and the arena works with no database and no keys.
 *
 * Three rules this file exists to enforce:
 *
 * 1. It is the *only* place either backend is named. Components take data as
 *    props and cannot tell the difference, which is what made swapping the
 *    backend a change to this file rather than a rewrite.
 *
 * 2. A configured database that errors throws. It must never fall back to the
 *    fixtures: those are invented questions, and serving them under the banner
 *    of real past papers is the worst thing this product could do.
 *
 * 3. The joins live in SQL, not here. PostgREST's embedded aggregates count
 *    rows visible in the embedded table rather than rows that survive the join,
 *    so a topic's question count assembled client-side silently includes
 *    questions still waiting on review.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { signedUrls } from "../storage";
import { reviewItems, reviewTopicOptions } from "./reviewSeed";
import * as seed from "./seed";
import type {
  Level,
  McqOption,
  McqQuestion,
  Paper,
  PaperDocument,
  QuestionType,
  ReviewFlag,
  ReviewItem,
  Season,
  Subject,
  Topic,
} from "./types";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

/** Both halves, or neither. A URL with no key produces a client that fails
 *  every request at runtime, which is a worse failure than not being configured
 *  at all — it looks connected right up until a page renders empty. */
export function isBackedByDatabase(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_KEY);
}

let cached: SupabaseClient | null | undefined;

function db(): SupabaseClient | null {
  if (cached === undefined) {
    cached = isBackedByDatabase()
      ? createClient(SUPABASE_URL, SUPABASE_KEY, {
          // Reads are anonymous and every page is statically generated; there
          // is no session to keep and nothing to write to storage.
          auth: { persistSession: false, autoRefreshToken: false },
        })
      : null;
  }
  return cached;
}

/** The service role bypasses row level security, so it is the only way to read
 *  a question that is not yet approved. Server-side only, and read through a
 *  window check rather than trusted to the bundler: the name carries no
 *  NEXT_PUBLIC_ prefix, but a mistake here would hand every visitor write
 *  access to every table. */
function serviceKey(): string {
  return typeof window === "undefined" ? process.env.SUPABASE_SERVICE_ROLE_KEY ?? "" : "";
}

let cachedService: SupabaseClient | null | undefined;

function serviceDb(): SupabaseClient | null {
  if (cachedService === undefined) {
    const key = serviceKey();
    cachedService =
      SUPABASE_URL && key
        ? createClient(SUPABASE_URL, key, {
            auth: { persistSession: false, autoRefreshToken: false },
          })
        : null;
  }
  return cachedService;
}

type Result<T> = { data: T[] | null; error: { message: string } | null };

async function rows<T>(what: string, query: PromiseLike<Result<T>>): Promise<T[]> {
  const { data, error } = await query;
  if (error) throw new Error(`reading ${what} from the database failed: ${error.message}`);
  return data ?? [];
}

/* ---------------------------------------------------------------------------
   View row shapes. These mirror db/migrations/0012_read_views.sql; a column
   renamed there is renamed in the mapper below.
   --------------------------------------------------------------------------- */

interface SubjectRow {
  slug: string;
  level_code: Subject["levelCode"];
  syllabus_code: string;
  title: string;
  description: string;
  is_published: boolean;
}

interface TopicRow {
  code: string;
  slug: string;
  title: string;
  learning_objectives: string[];
  question_count: number;
  parent_code: string | null;
  is_revisable: boolean;
}

interface PaperRow {
  slug: string;
  subject_slug: string;
  year: number;
  season: Season;
  component: number;
  variant: number | null;
  question_type: QuestionType | null;
  question_count: number;
  documents: PaperDocument[];
}

interface McqRow {
  id: string;
  paper_slug: string;
  display_label: string;
  question_text: string | null;
  options: Partial<Record<McqOption, string>>;
  correct_option: McqOption;
  mark_scheme_text: string | null;
  examiner_comment: string | null;
  topic_codes: string[];
  crop_storage_key: string | null;
}

const toSubject = (row: SubjectRow): Subject => ({
  slug: row.slug,
  levelCode: row.level_code,
  syllabusCode: row.syllabus_code,
  title: row.title,
  description: row.description,
  isPublished: row.is_published,
});

const toTopic = (row: TopicRow): Topic => ({
  code: row.code,
  slug: row.slug,
  title: row.title,
  learningObjectives: row.learning_objectives,
  questionCount: row.question_count,
  parentCode: row.parent_code,
  isRevisable: row.is_revisable,
});

const toPaper = (row: PaperRow): Paper => ({
  slug: row.slug,
  subjectSlug: row.subject_slug,
  year: row.year,
  season: row.season,
  component: row.component,
  variant: row.variant,
  // A paper nobody has said anything about yet reads as structured: the
  // consequence is that it is not offered as timed practice, which is the safe
  // direction to be wrong in.
  questionType: row.question_type ?? "structured",
  questionCount: row.question_count,
  documents: row.documents,
});

const toMcq = (row: McqRow): McqQuestion => ({
  id: row.id,
  paperSlug: row.paper_slug,
  displayLabel: row.display_label,
  questionText: row.question_text ?? "",
  options: row.options as Record<McqOption, string>,
  correctOption: row.correct_option,
  markScheme: row.mark_scheme_text,
  examinerNote: row.examiner_comment,
  topicCodes: row.topic_codes,
  cropUrl: assetUrl(row.crop_storage_key),
});

/** The stable, unsigned URL a page can carry. /api/asset checks the question is
 *  approved and redirects to a freshly signed one, so a prerendered page never
 *  holds a link that expires. */
function assetUrl(storageKey: string | null): string | null {
  if (!storageKey) return null;
  return `/api/asset/${storageKey.split("/").map(encodeURIComponent).join("/")}`;
}

const SUBJECT_COLUMNS = "slug,level_code,syllabus_code,title,description,is_published";
const TOPIC_COLUMNS =
  "code,slug,title,learning_objectives,question_count,parent_code,is_revisable";
const PAPER_COLUMNS =
  "slug,subject_slug,year,season,component,variant,question_type,question_count,documents";
// One string literal, not a concatenation: supabase-js infers the row type from
// the literal text of the column list, and `"a," + "b"` widens to `string`.
const MCQ_COLUMNS =
  "id,paper_slug,display_label,question_text,options,correct_option,mark_scheme_text,examiner_comment,topic_codes,crop_storage_key";

/* ---------------------------------------------------------------------------
   Catalogue
   --------------------------------------------------------------------------- */

export async function getLevels(): Promise<Level[]> {
  const client = db();
  if (!client) return seed.levels;

  return rows<Level>(
    "levels",
    client.from("levels").select("code,name,slug").order("sort_order"),
  );
}

export async function getSubjects(): Promise<Subject[]> {
  const client = db();
  if (!client) return seed.subjects;

  const result = await rows<SubjectRow>(
    "subjects",
    client
      .from("v_subjects")
      .select(SUBJECT_COLUMNS)
      .order("level_sort_order")
      .order("title"),
  );
  return result.map(toSubject);
}

export async function getSubjectsByLevel(levelCode: string): Promise<Subject[]> {
  const client = db();
  if (!client) return seed.subjects.filter((s) => s.levelCode === levelCode);

  const result = await rows<SubjectRow>(
    "subjects",
    client
      .from("v_subjects")
      .select(SUBJECT_COLUMNS)
      .eq("level_code", levelCode)
      .order("title"),
  );
  return result.map(toSubject);
}

export async function getSubject(slug: string): Promise<Subject | null> {
  const client = db();
  if (!client) return seed.subjects.find((s) => s.slug === slug) ?? null;

  const [row] = await rows<SubjectRow>(
    "a subject",
    client.from("v_subjects").select(SUBJECT_COLUMNS).eq("slug", slug).limit(1),
  );
  return row ? toSubject(row) : null;
}

export async function getTopics(subjectSlug: string): Promise<Topic[]> {
  const client = db();
  if (!client) return subjectSlug === "physics-5054" ? seed.topics : [];

  const result = await rows<TopicRow>(
    "topics",
    client
      .from("v_topics")
      .select(TOPIC_COLUMNS)
      .eq("subject_slug", subjectSlug)
      .order("sort_order")
      .order("code"),
  );
  // The whole tree, containers included — they are the headings the sidebar
  // nests under. Callers that mean "a topic someone can revise" say so with
  // `revisableTopics`; a container is not one, and linking to it would be a
  // link to an empty page.
  return result.map(toTopic);
}

/** The nodes that carry learning outcomes: what a student revises, what a
 *  question is tagged against, and the only ones with a page of their own. */
export async function revisableTopics(subjectSlug: string): Promise<Topic[]> {
  const topics = await getTopics(subjectSlug);
  return topics.filter((topic) => topic.isRevisable);
}

export async function getTopic(
  subjectSlug: string,
  topicSlug: string,
): Promise<Topic | null> {
  const topics = await getTopics(subjectSlug);
  // A container has no page: no outcomes to list and nothing tagged to it.
  return topics.find((t) => t.slug === topicSlug && t.isRevisable) ?? null;
}

export async function getPapers(subjectSlug: string): Promise<Paper[]> {
  const client = db();
  if (!client) {
    return seed.papers
      .filter((p) => p.subjectSlug === subjectSlug)
      .sort((a, b) => b.year - a.year || a.component - b.component);
  }

  const result = await rows<PaperRow>(
    "papers",
    client
      .from("v_papers")
      .select(PAPER_COLUMNS)
      .eq("subject_slug", subjectSlug)
      .order("year", { ascending: false })
      .order("component")
      .order("variant"),
  );
  return result.map(toPaper);
}

export async function getPaper(slug: string): Promise<Paper | null> {
  const client = db();
  if (!client) return seed.papers.find((p) => p.slug === slug) ?? null;

  const [row] = await rows<PaperRow>(
    "a paper",
    client.from("v_papers").select(PAPER_COLUMNS).eq("slug", slug).limit(1),
  );
  return row ? toPaper(row) : null;
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
  const client = db();
  if (!client) return seed.mcqQuestions.filter((q) => q.paperSlug === paperSlug);

  const result = await rows<McqRow>(
    "questions",
    client
      .from("v_mcq_questions")
      .select(MCQ_COLUMNS)
      .eq("paper_slug", paperSlug)
      .order("ordinal"),
  );
  return result.map(toMcq);
}

/** Questions for one topic, across every paper — the topical engine. */
export async function getQuestionsByTopic(
  subjectSlug: string,
  topicCode: string,
): Promise<McqQuestion[]> {
  const client = db();
  if (!client) {
    const papers = new Set(
      seed.papers.filter((p) => p.subjectSlug === subjectSlug).map((p) => p.slug),
    );
    return seed.mcqQuestions.filter(
      (q) => papers.has(q.paperSlug) && q.topicCodes.includes(topicCode),
    );
  }

  const result = await rows<McqRow>(
    "questions for a topic",
    client
      .from("v_mcq_questions")
      .select(MCQ_COLUMNS)
      .eq("subject_slug", subjectSlug)
      .contains("topic_codes", [topicCode])
      .order("paper_slug")
      .order("ordinal"),
  );
  return result.map(toMcq);
}

/** Papers that have MCQs loaded and are therefore playable in the arena. */
export async function getPlayablePapers(subjectSlug: string): Promise<Paper[]> {
  const client = db();
  if (!client) {
    const papers = await getPapers(subjectSlug);
    const loaded = new Set(seed.mcqQuestions.map((q) => q.paperSlug));
    return papers.filter((p) => loaded.has(p.slug));
  }

  const result = await rows<PaperRow>(
    "playable papers",
    client
      .from("v_papers")
      .select(PAPER_COLUMNS)
      .eq("subject_slug", subjectSlug)
      .eq("question_type", "mcq")
      // A paper with no approved questions is listed but not playable: an arena
      // that opens on an empty paper is worse than one not offered at all.
      .gt("question_count", 0)
      .order("year", { ascending: false })
      .order("component"),
  );
  return result.map(toPaper);
}

/* ---------------------------------------------------------------------------
   Review queue
   --------------------------------------------------------------------------- */

/** PostgREST's own per-response cap. Asking for more in one call silently
 *  returns 1000 rows and no indication that it truncated. */
const REVIEW_PAGE = 1000;

/** A ceiling on how much of the queue one page will hold in memory. The whole
 *  queue is rendered at once today, which is fine for a few thousand and will
 *  not be for a backfill — at that point this needs paging in the UI, not a
 *  bigger number here. */
const REVIEW_QUEUE_MAX = 5000;

interface ReviewRow {
  id: string;
  paper_slug: string;
  paper_title: string;
  display_label: string;
  question_type: QuestionType;
  question_text: string | null;
  mark_scheme_text: string | null;
  correct_option: McqOption | null;
  crop_storage_key: string | null;
  page_number: number | null;
  extraction_confidence: number | null;
  extraction_status: "extracted" | "needs_review";
  review_flags: ReviewFlag[];
  proposed_topics: { code: string; title: string; confidence: number }[];
}

/** Questions the pipeline extracted but would not publish on its own.
 *
 *  Flagged questions first, then worst confidence: the queue is the reviewer's
 *  whole working day, so the questions most likely to be wrong have to be the
 *  ones at the top of it.
 *
 *  This is the one read row level security cannot serve — every row in it is
 *  unapproved by definition — so it needs the service role. Without that key
 *  the page shows the fixtures, clearly labelled, rather than an empty queue
 *  that would read as "nothing left to review". */
export async function getReviewQueue(): Promise<ReviewItem[]> {
  const client = serviceDb();
  if (!client) {
    return [...reviewItems].sort(
      (a, b) => a.extractionConfidence - b.extractionConfidence,
    );
  }

  // Paged, because PostgREST caps a response at 1000 rows and says nothing
  // about it. Unpaged, a queue of 1240 reported "1000 pending" and the last 240
  // questions were unreachable — invisible, un-reviewable, and therefore never
  // publishable, with nothing on screen to suggest they existed.
  const result: ReviewRow[] = [];
  for (let from = 0; from < REVIEW_QUEUE_MAX; from += REVIEW_PAGE) {
    const page = await rows<ReviewRow>(
      "the review queue",
      client
        .from("v_review_queue")
        .select("*")
        // needs_review sorts before extracted alphabetically by luck rather
        // than by design, so the order is spelled out below instead.
        .order("extraction_confidence", { nullsFirst: true })
        .order("id")
        .range(from, from + REVIEW_PAGE - 1),
    );
    result.push(...page);
    if (page.length < REVIEW_PAGE) break;
  }

  const flaggedFirst = [...result].sort((a, b) => {
    const flagged = Number(b.extraction_status === "needs_review") -
      Number(a.extraction_status === "needs_review");
    return flagged || (a.extraction_confidence ?? 0) - (b.extraction_confidence ?? 0);
  });

  // Signed here rather than through /api/asset: every row in this queue is
  // unapproved, which is exactly what that route refuses. One request for the
  // whole page — a hundred crops signed one at a time is a hundred round trips
  // before anything renders.
  const urls = await signedUrls(
    flaggedFirst.map((row) => row.crop_storage_key).filter((key): key is string =>
      Boolean(key),
    ),
  );

  return flaggedFirst.map((row) => ({
    id: row.id,
    paperSlug: row.paper_slug,
    paperTitle: row.paper_title,
    displayLabel: row.display_label,
    questionType: row.question_type,
    pageNumber: row.page_number ?? 0,
    questionText: row.question_text ?? "",
    markScheme: row.mark_scheme_text,
    correctOption: row.correct_option,
    cropStorageKey: row.crop_storage_key,
    cropUrl: (row.crop_storage_key && urls.get(row.crop_storage_key)) || null,
    extractionConfidence: row.extraction_confidence ?? 0,
    flags: row.review_flags,
    proposedTopics: row.proposed_topics.map((topic) => ({
      ...topic,
      // The classifier's reasoning is not stored yet; the reviewer sees the
      // code, the title and how sure the model was.
      reasoning: "",
    })),
  }));
}

export async function getReviewTopicOptions(): Promise<
  { code: string; title: string }[]
> {
  const client = db();
  if (!client) return reviewTopicOptions;

  const topics = await revisableTopics("physics-5054");
  return topics.map(({ code, title }) => ({ code, title }));
}

/** Is this crop attached to a question a student is allowed to see?
 *
 *  Asked through the anonymous client on purpose: its view of `questions` is
 *  whatever row level security allows, which is approved rows only. The answer
 *  therefore comes from the same policy that protects the question itself,
 *  rather than from a second rule that has to be kept in step with it. */
export async function assetIsPublic(storageKey: string): Promise<boolean> {
  const client = db();
  // With no database configured there is no private bank to protect and no
  // storage to serve from; the fixtures carry no crops at all.
  if (!client) return false;

  const { data, error } = await client
    .from("question_assets")
    .select("storage_key,questions!inner(id)")
    .eq("storage_key", storageKey)
    .limit(1);

  if (error) {
    console.warn(`checking ${storageKey} failed: ${error.message}`);
    return false;
  }
  return (data?.length ?? 0) > 0;
}

export const SEED_NOTICE = seed.SEED_NOTICE;
