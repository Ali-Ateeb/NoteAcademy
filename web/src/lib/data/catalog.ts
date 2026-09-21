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
  DecidedItem,
  DuplicateRef,
  Level,
  McqOption,
  McqQuestion,
  Paper,
  PaperDocument,
  QuestionType,
  ReviewFlag,
  ReviewItem,
  Season,
  StructuredPart,
  StructuredQuestion,
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
  total_marks: number;
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

interface StructuredCropRow {
  pageNumber: number;
  storageKey: string;
}

interface StructuredPartRow {
  displayLabel: string;
  maxMarks: number | null;
  markSchemeText: string | null;
}

interface StructuredRow {
  id: string;
  paper_slug: string;
  display_label: string;
  question_text: string | null;
  mark_scheme: string | null;
  max_marks: number | null;
  crops: StructuredCropRow[];
  parts: StructuredPartRow[];
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
  also_in: DuplicateRef[];
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
  totalMarks: row.total_marks,
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
  alsoIn: row.also_in ?? [],
});

const toStructuredPart = (part: StructuredPartRow): StructuredPart => ({
  displayLabel: part.displayLabel,
  maxMarks: part.maxMarks,
  markSchemeText: part.markSchemeText,
});

const toStructured = (row: StructuredRow): StructuredQuestion => ({
  id: row.id,
  paperSlug: row.paper_slug,
  displayLabel: row.display_label,
  questionText: row.question_text ?? "",
  markScheme: row.mark_scheme,
  maxMarks: row.max_marks,
  crops: row.crops
    .map((crop) => ({ pageNumber: crop.pageNumber, cropUrl: assetUrl(crop.storageKey) }))
    .sort((a, b) => a.pageNumber - b.pageNumber),
  parts: row.parts.map(toStructuredPart),
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
  "code,slug,title,learning_objectives,question_count,total_marks,parent_code,is_revisable";
const PAPER_COLUMNS =
  "slug,subject_slug,year,season,component,variant,question_type,question_count,documents";
// One string literal, not a concatenation: supabase-js infers the row type from
// the literal text of the column list, and `"a," + "b"` widens to `string`.
const MCQ_COLUMNS =
  "id,paper_slug,display_label,question_text,options,correct_option,mark_scheme_text,examiner_comment,topic_codes,crop_storage_key,also_in";
const STRUCTURED_COLUMNS =
  "id,paper_slug,display_label,question_text,mark_scheme,max_marks,crops,parts";

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

/** A structured paper's top-level questions — the practice unit, each one
 *  carrying every part and sub-part underneath it. See v_structured_questions
 *  (db/migrations/0023) for why this is not simply every row of the paper. */
export async function getStructuredQuestions(
  paperSlug: string,
): Promise<StructuredQuestion[]> {
  const client = db();
  if (!client) {
    return seed.structuredQuestions.filter((q) => q.paperSlug === paperSlug);
  }

  const result = await rows<StructuredRow>(
    "structured questions",
    client
      .from("v_structured_questions")
      .select(STRUCTURED_COLUMNS)
      .eq("paper_slug", paperSlug)
      .order("ordinal"),
  );
  return result.map(toStructured);
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
      // Cross-paper browsing is exactly where a duplicate would otherwise
      // cost a student's attention twice: `getMcqQuestions` (one paper, the
      // timed arena) deliberately does not apply this filter, because a
      // paper's own question count has to match what was printed regardless
      // of what another paper's variant shares with it.
      .is("canonical_question_id", null)
      .order("paper_slug")
      .order("ordinal"),
  );
  return result.map(toMcq);
}

/** Every playable MCQ across several topics at once, folded to one query and
 *  one duplicate-free list — the weak-topic revision queue's own population.
 *  Built for exactly that caller: `getQuestionsByTopic` (singular) stays as
 *  it is for the existing one-topic-at-a-time pages rather than becoming a
 *  one-element call into this, since a single extra array allocation per
 *  request buys nothing there. */
export async function getQuestionsByTopics(
  subjectSlug: string,
  topicCodes: string[],
): Promise<McqQuestion[]> {
  if (topicCodes.length === 0) return [];

  const client = db();
  if (!client) {
    const papers = new Set(
      seed.papers.filter((p) => p.subjectSlug === subjectSlug).map((p) => p.slug),
    );
    const codes = new Set(topicCodes);
    return seed.mcqQuestions.filter(
      (q) => papers.has(q.paperSlug) && q.topicCodes.some((code) => codes.has(code)),
    );
  }

  const result = await rows<McqRow>(
    "questions across several topics",
    client
      .from("v_mcq_questions")
      .select(MCQ_COLUMNS)
      .eq("subject_slug", subjectSlug)
      // Array-overlap (Postgres &&), not .contains: this wants a question
      // tagged to *any* of the given topics, not all of them.
      .overlaps("topic_codes", topicCodes)
      .is("canonical_question_id", null)
      .order("paper_slug")
      .order("ordinal"),
  );
  return result.map(toMcq);
}

/** Which subject and topics a set of questions belong to — for the student's
 *  own dashboard, which knows only the ids of what they have attempted.
 *
 *  This replaced a per-subject dump of every question's topics, which was
 *  serialised into the page for every visitor (about 100 kB of inline props
 *  on the Physics dashboard) so the browser could look up a few dozen ids.
 *  Asking only about the attempted ids moves that cost to the students who
 *  have attempted something, and scales with their history rather than the
 *  size of the question bank.
 *
 *  Ids that are not approved multiple-choice questions simply do not come
 *  back — the same set the dashboard has always measured. */
export async function getQuestionMetaByIds(
  ids: string[],
): Promise<{ id: string; subjectSlug: string; topicCodes: string[] }[]> {
  if (ids.length === 0) return [];

  const client = db();
  if (!client) {
    const subjectByPaper = new Map(seed.papers.map((p) => [p.slug, p.subjectSlug]));
    const wanted = new Set(ids);
    return seed.mcqQuestions
      .filter((q) => wanted.has(q.id))
      .map((q) => ({
        id: q.id,
        subjectSlug: subjectByPaper.get(q.paperSlug) ?? "",
        topicCodes: q.topicCodes,
      }));
  }

  // A local attempt log can hold an id from an older schema or a hand-edited
  // value. Postgres rejects a non-uuid against a uuid column and would fail the
  // whole chunk with it, so such ids are dropped here: they could never match
  // a question anyway.
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const valid = ids.filter((id) => UUID.test(id));

  // `in.(…)` travels in the URL, so a long list is chunked: 100 uuids is about
  // 4 kB, comfortably inside every proxy's limit, and the chunks run together.
  const CHUNK = 100;
  const chunks: string[][] = [];
  for (let i = 0; i < valid.length; i += CHUNK) chunks.push(valid.slice(i, i + CHUNK));

  const results = await Promise.all(
    chunks.map((chunk) =>
      rows<{ id: string; subject_slug: string; topic_codes: string[] }>(
        "question topics for attempted questions",
        client.from("v_mcq_questions").select("id,subject_slug,topic_codes").in("id", chunk),
      ),
    ),
  );
  return results.flat().map((row) => ({
    id: row.id,
    subjectSlug: row.subject_slug,
    topicCodes: row.topic_codes,
  }));
}

/** Papers with questions loaded and therefore playable in an arena — timed
 *  multiple-choice or self-marked structured, either one. */
export async function getPlayablePapers(subjectSlug: string): Promise<Paper[]> {
  const client = db();
  if (!client) {
    const papers = await getPapers(subjectSlug);
    const loaded = new Set([
      ...seed.mcqQuestions.map((q) => q.paperSlug),
      ...seed.structuredQuestions.map((q) => q.paperSlug),
    ]);
    return papers.filter((p) => loaded.has(p.slug));
  }

  const result = await rows<PaperRow>(
    "playable papers",
    client
      .from("v_papers")
      .select(PAPER_COLUMNS)
      .eq("subject_slug", subjectSlug)
      .in("question_type", ["mcq", "structured"])
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

/** One page's worth of the queue. Small enough that even the "flagged and
 *  least confident first" rows — the ones a reviewer is most likely to sit
 *  with for a while — render and sign instantly; large enough that paging
 *  through even a ten-subject backlog is a handful of loads, not hundreds. */
export const REVIEW_PAGE_SIZE = 200;

export interface ReviewQueueFilter {
  subjectSlug?: string;
  flag?: ReviewFlag;
}

export interface ReviewQueuePage {
  items: ReviewItem[];
  /** Rows matching the filter across the *whole* queue, not just this page —
   *  what "N pending" and "load more" are both computed from. */
  total: number;
}

interface ReviewCropRow {
  storageKey: string;
  pageNumber: number;
}

interface ReviewPartRow {
  displayLabel: string;
  maxMarks: number | null;
  markSchemeText: string | null;
  reviewFlags: ReviewFlag[];
}

interface ReviewRow {
  id: string;
  subject_slug: string;
  paper_slug: string;
  paper_title: string;
  display_label: string;
  question_type: QuestionType;
  question_text: string | null;
  mark_scheme_text: string | null;
  correct_option: McqOption | null;
  crops: ReviewCropRow[];
  parts: ReviewPartRow[] | null;
  extraction_confidence: number | null;
  extraction_status: "extracted" | "needs_review";
  review_flags: ReviewFlag[];
  proposed_topics: { code: string; title: string; confidence: number }[];
}

function filterFixtureItems(filter: ReviewQueueFilter): ReviewItem[] {
  return [...reviewItems]
    .sort((a, b) => (a.proposedTopics[0]?.confidence ?? 0) - (b.proposedTopics[0]?.confidence ?? 0))
    .filter((item) => !filter.subjectSlug || item.paperSlug.startsWith(`${filter.subjectSlug}-`))
    .filter((item) => !filter.flag || item.flags.includes(filter.flag as ReviewFlag));
}

/** One page of questions the pipeline extracted but would not publish on its
 *  own, optionally scoped to one subject or one review flag.
 *
 *  Flagged questions first, then worst topic confidence: the queue is the
 *  reviewer's whole working day, so the questions most likely to be wrong
 *  have to be the ones at the top of it. Both are columns on `v_review_queue`
 *  itself (0028) precisely so this ordering — and the paging below — is a
 *  plain `ORDER BY ... LIMIT ... OFFSET`, not "fetch everything and sort in
 *  JavaScript". The old approach capped out at 5000 rows and silently
 *  truncated past it; this has no such ceiling, because no request ever
 *  asks for more than `limit` rows at a time.
 *
 *  This is the one read row level security cannot serve — every row in it is
 *  unapproved by definition — so it needs the service role. Without that key
 *  the page shows the fixtures, clearly labelled, rather than an empty queue
 *  that would read as "nothing left to review". */
export async function getReviewQueue(
  filter: ReviewQueueFilter = {},
  offset = 0,
  limit: number = REVIEW_PAGE_SIZE,
): Promise<ReviewQueuePage> {
  const client = serviceDb();
  if (!client) {
    const items = filterFixtureItems(filter);
    return { items: items.slice(offset, offset + limit), total: items.length };
  }

  let query = client
    .from("v_review_queue")
    .select("*", { count: "exact" })
    .order("is_flagged", { ascending: false })
    // Least confident first. Not extraction_confidence, which is 1.0 on every
    // multiple-choice question the geometric segmenter accepted — the
    // segmenter either matches the template or the paper is refused, so
    // there is no middle, and sorting by a constant is not sorting it. The
    // number that varies, and the only one a reviewer can act on, is how
    // sure the classifier was about the topic.
    .order("primary_topic_confidence", { ascending: true, nullsFirst: true })
    .order("id")
    .range(offset, offset + limit - 1);

  if (filter.subjectSlug) query = query.eq("subject_slug", filter.subjectSlug);
  if (filter.flag) query = query.contains("review_flags", [filter.flag]);

  const { data, error, count } = await query;
  if (error) throw new Error(`reading the review queue from the database failed: ${error.message}`);
  const result = (data ?? []) as unknown as ReviewRow[];

  // Signed here rather than through /api/asset: every row in this queue is
  // unapproved, which is exactly what that route refuses. One request for the
  // whole page — a hundred crops signed one at a time is a hundred round trips
  // before anything renders. A structured question can carry several of its
  // own, so this flattens every row's crops rather than just the first.
  // Only this page's crops, not the whole filtered queue — the other thing
  // paging fixes: the old code signed URLs for every unapproved question in
  // the database on every single page load, whether or not it was ever shown.
  const urls = await signedUrls(
    result.flatMap((row) => row.crops.map((crop) => crop.storageKey)),
  );

  const items: ReviewItem[] = result.map((row) => ({
    id: row.id,
    subjectSlug: row.subject_slug,
    paperSlug: row.paper_slug,
    paperTitle: row.paper_title,
    displayLabel: row.display_label,
    questionType: row.question_type,
    questionText: row.question_text ?? "",
    markScheme: row.mark_scheme_text,
    correctOption: row.correct_option,
    crops: row.crops.map((crop) => ({
      storageKey: crop.storageKey,
      pageNumber: crop.pageNumber,
      url: urls.get(crop.storageKey) ?? null,
    })),
    parts:
      row.parts &&
      row.parts.map((part) => ({
        displayLabel: part.displayLabel,
        maxMarks: part.maxMarks,
        markSchemeText: part.markSchemeText,
        reviewFlags: part.reviewFlags,
      })),
    extractionConfidence: row.extraction_confidence ?? 0,
    flags: row.review_flags,
    proposedTopics: row.proposed_topics.map((topic) => ({
      ...topic,
      // The classifier's reasoning is not stored yet; the reviewer sees the
      // code, the title and how sure the model was.
      reasoning: "",
    })),
  }));

  return { items, total: count ?? 0 };
}

/** How many decided questions the admin lookup shows. A reviewer looking for
 *  one they just decided needs the most recent handful, not a full audit log
 *  — and after a bulk approval this table can hold thousands of rows with the
 *  same `reviewed_at` batch, where a bigger number would not help. */
const DECIDED_LIMIT = 300;

interface DecidedRow {
  id: string;
  paper_slug: string;
  paper_title: string;
  display_label: string;
  extraction_status: "approved" | "rejected";
  reviewed_at: string | null;
  primary_topic: { code: string; title: string; confidence: number } | null;
}

/** Questions that have already left the queue, newest decision first — the
 *  lookup for "I approved this and the topic was wrong", since the queue
 *  itself stops listing a question the moment it is decided.
 *
 *  Same service-role surface as `getReviewQueue`, and for the same reason: a
 *  rejected question is unapproved by definition, so anyone without the
 *  service role sees none of this either way. */
export async function getDecidedQuestions(): Promise<DecidedItem[]> {
  const client = serviceDb();
  if (!client) return [];

  const result = await rows<DecidedRow>(
    "decided questions",
    client
      .from("v_review_decided")
      .select("*")
      .order("reviewed_at", { ascending: false, nullsFirst: false })
      .limit(DECIDED_LIMIT),
  );

  return result.map((row) => ({
    id: row.id,
    paperSlug: row.paper_slug,
    paperTitle: row.paper_title,
    displayLabel: row.display_label,
    decision: row.extraction_status,
    reviewedAt: row.reviewed_at,
    primaryTopic: row.primary_topic,
  }));
}

/** The topics a reviewer may assign.
 *
 *  Read with the service role, not the anonymous client. `v_topics` joins
 *  `subjects`, whose policy admits only published ones — and a subject is
 *  published *after* its bank has been reviewed, never before. Going through
 *  the public path meant the reviewer's topic list was empty for precisely the
 *  subject they were reviewing, with no error: sixty-three topics silently
 *  became none, and the only way to change a wrong tag disappeared with them.
 *
 *  The queue is a service-role surface throughout. This is part of it. */
export async function getReviewTopicOptions(
  subjectSlug = "physics-5054",
): Promise<{ code: string; title: string }[]> {
  const client = serviceDb();
  if (!client) return reviewTopicOptions;

  const result = await rows<{ code: string; title: string }>(
    "topic options",
    client
      .from("v_topics")
      .select("code,title,sort_order,is_revisable")
      .eq("subject_slug", subjectSlug)
      .eq("is_revisable", true)
      .order("sort_order")
      .order("code"),
  );
  return result.map(({ code, title }) => ({ code, title }));
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
