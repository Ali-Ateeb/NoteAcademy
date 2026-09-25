/**
 * Reviewer decisions, written to Postgres.
 *
 * This is the only write path in the application, and it is the one that
 * decides what students see: approving a question publishes it. So it is worth
 * being explicit about what protects it.
 *
 * Every request here has to be a signed-in account with `profiles.is_reviewer
 * = true` (`currentReviewer()`, checked against the caller's own session
 * cookie — see `@/lib/reviewerAuth`). That authorization check runs under the
 * caller's own identity, subject to RLS the same as any other session; the
 * write itself still needs the service role, because RLS grants
 * `authenticated` no UPDATE on `questions` or `question_topics` at all — but
 * the *decision* to allow the write is made first, as the real account making
 * the request, and that account's id is what lands in `reviewed_by`. This
 * replaced a shared REVIEW_TOKEN secret (see 0029_reviewer_accounts.sql):
 * every decision now has an attributable identity behind it instead of "the
 * one string, known to whoever has it."
 *
 * Every decision here also revalidates the static pages it affects
 * (`revalidatePath`, see `revalidateForQuestion` below) — approving a
 * question through this route is meant to be visible the same request, not
 * after whatever timed ISR window the page itself declares (`revalidate =
 * 900` in papers/[paper]/page.tsx and its five siblings). That timed
 * fallback still exists for the path this route cannot reach: `noteacademy
 * bulk-approve` writes straight to Postgres with psycopg, outside the
 * Next.js runtime entirely, so there is no request here to revalidate from.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";

import type { ReviewDecision } from "@/lib/data/types";
import { currentReviewer, type Reviewer } from "@/lib/reviewerAuth";
import { publicCropsEnabled, publishCrops, unpublishCrops } from "@/lib/storage";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";

/** The statuses a decision may be made from, and returned to on undo.
 *  'approved' is not in the list: re-deciding an approved question is a
 *  deliberate act, not something a stray request should do. */
const UNDECIDED = ["extracted", "needs_review"] as const;

function serviceClient(): SupabaseClient | null {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";
  if (!SUPABASE_URL || !key) return null;
  return createClient(SUPABASE_URL, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/** Every handler below starts with this: prove the caller is a reviewer
 *  before doing anything else. Indistinguishable from "no such question" or
 *  any other refusal on purpose — whether a question is merely awaiting
 *  review is not something to leak to a request that never proved it was
 *  allowed to ask. */
async function requireReviewer(): Promise<
  { reviewer: Reviewer; refusal: null } | { reviewer: null; refusal: NextResponse }
> {
  const reviewer = await currentReviewer();
  if (!reviewer) {
    return { reviewer: null, refusal: NextResponse.json({ error: "Not authorised." }, { status: 401 }) };
  }
  return { reviewer, refusal: null };
}

/** One row in the group a decision applies to, with just enough of it to
 *  decide what that row's own restored status should be on undo. */
interface GroupRow {
  id: string;
  reviewFlags: string[];
}

/** Every row a decision on `questionId` should actually touch.
 *
 *  For everything but a structured question this is just the row itself. A
 *  structured question is a tree — marks and mark-scheme text live on its
 *  leaves, not on "9" itself — but the review queue shows one card per
 *  top-level question (see db/migrations/0022), and approving that card
 *  means signing off on the whole thing: the crop, and every part and
 *  sub-part it covers. Scoped by a `like` on `display_label` rather than a
 *  recursive walk of `parent_question_id`: "10(a)" cannot match a pattern
 *  built from "1", because the pattern requires the literal "(" immediately
 *  after the number, so a plain prefix match cannot cross from one top-level
 *  question into a sibling with a shared leading digit.
 */
/** The storage keys of every crop attached to these questions. */
async function cropKeysFor(client: SupabaseClient, questionIds: string[]): Promise<string[]> {
  const { data } = await client
    .from("question_assets")
    .select("storage_key")
    .in("question_id", questionIds);
  return (data ?? []).map((row) => (row as { storage_key: string }).storage_key);
}

async function resolveGroup(
  client: SupabaseClient,
  questionId: string,
): Promise<GroupRow[] | null> {
  const { data: question } = await client
    .from("questions")
    .select("id,paper_id,display_label,question_type,review_flags")
    .eq("id", questionId)
    .maybeSingle();
  if (!question) return null;

  const q = question as {
    id: string;
    paper_id: string;
    display_label: string;
    question_type: string;
    review_flags: string[];
  };
  const self: GroupRow = { id: q.id, reviewFlags: q.review_flags ?? [] };
  if (q.question_type !== "structured") return [self];

  const { data: descendants, error } = await client
    .from("questions")
    .select("id,review_flags")
    .eq("paper_id", q.paper_id)
    .like("display_label", `${q.display_label}(%`);
  if (error) return [self];

  return [
    self,
    ...(descendants ?? []).map((row) => ({
      id: (row as { id: string; review_flags: string[] }).id,
      reviewFlags: (row as { id: string; review_flags: string[] }).review_flags ?? [],
    })),
  ];
}

/** Every static page a decision on `questionId` could change, revalidated
 *  immediately rather than left to `CONTENT_REVALIDATE_SECONDS`.
 *
 *  Reads the question's *current* state — called after whatever write
 *  already happened, so a topic reassignment is reflected correctly without
 *  this needing to know what changed, only what the result is now.
 *  `extraTopicSlugs` is for the one thing a fresh read cannot see: a topic a
 *  retag just moved the question *off* of, whose drill page has to lose the
 *  question exactly as immediately as the new one gains it. */
async function revalidateForQuestion(
  client: SupabaseClient,
  questionId: string,
  extraTopicSlugs: string[] = [],
): Promise<void> {
  const { data: question } = await client
    .from("questions")
    .select("paper_id")
    .eq("id", questionId)
    .maybeSingle();
  if (!question) return;

  const { data: paper } = await client
    .from("papers")
    .select("slug,subject_id")
    .eq("id", (question as { paper_id: string }).paper_id)
    .maybeSingle();
  if (!paper) return;
  const { slug: paperSlug, subject_id: subjectId } = paper as {
    slug: string;
    subject_id: string;
  };

  revalidatePath(`/papers/${paperSlug}`);
  revalidatePath(`/practice/${paperSlug}`);

  const { data: subject } = await client
    .from("subjects")
    .select("slug")
    .eq("id", subjectId)
    .maybeSingle();
  const subjectSlug = subject ? (subject as { slug: string }).slug : null;
  if (!subjectSlug) return;

  revalidatePath(`/subjects/${subjectSlug}`);
  revalidatePath(`/dashboard/${subjectSlug}`);

  const { data: topicRows } = await client
    .from("question_topics")
    .select("topics(slug)")
    .eq("question_id", questionId);

  const slugs = new Set(extraTopicSlugs);
  for (const row of topicRows ?? []) {
    // question_topics.topic_id is a single not-null FK, so PostgREST embeds
    // `topics` as one object at runtime (confirmed against the live
    // database) — supabase-js's un-generated types guess an array instead,
    // which is what the `unknown` hop below is working around, not a real
    // runtime ambiguity.
    const slug = (row as unknown as { topics: { slug: string } | null }).topics?.slug;
    if (slug) slugs.add(slug);
  }
  for (const slug of slugs) {
    revalidatePath(`/topics/${subjectSlug}/${slug}`);
    revalidatePath(`/topics/${subjectSlug}/${slug}/practice`);
  }
}

interface DecisionBody {
  questionId?: unknown;
  decision?: unknown;
  topicCode?: unknown;
}

export async function POST(request: Request) {
  const auth = await requireReviewer();
  if (auth.refusal) return auth.refusal;

  const client = serviceClient();
  if (!client) {
    return NextResponse.json({ error: "No database configured." }, { status: 503 });
  }

  let body: DecisionBody;
  try {
    body = (await request.json()) as DecisionBody;
  } catch {
    return NextResponse.json({ error: "Expected JSON." }, { status: 400 });
  }

  const questionId = typeof body.questionId === "string" ? body.questionId : "";
  const decision = body.decision as ReviewDecision;
  const topicCode = typeof body.topicCode === "string" ? body.topicCode : null;

  if (!questionId || (decision !== "approved" && decision !== "rejected")) {
    return NextResponse.json({ error: "Bad decision." }, { status: 400 });
  }

  const group = await resolveGroup(client, questionId);
  if (!group) {
    return NextResponse.json({ error: "No such question." }, { status: 404 });
  }

  // Only an undecided question can be decided. Without this a replayed request
  // could quietly re-approve something a reviewer had already rejected. For a
  // structured question this updates the whole group at once — the card the
  // reviewer approved and every part and sub-part under it — so the decision
  // reaches every row the crop actually covers, not just the top-level one.
  const { data, error } = await client
    .from("questions")
    .update({
      extraction_status: decision,
      reviewed_at: new Date().toISOString(),
      reviewed_by: auth.reviewer.id,
    })
    .in("id", group.map((row) => row.id))
    .in("extraction_status", UNDECIDED)
    .select("id");

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
  // The row the reviewer actually decided on has to be among what moved — a
  // structured question's *parts* being already decided is not this check's
  // business, but the question itself already being decided is exactly the
  // replayed-request case above.
  if (!data?.some((row) => (row as { id: string }).id === questionId)) {
    return NextResponse.json(
      { error: "That question is not awaiting a decision." },
      { status: 409 },
    );
  }

  // A rejected second opinion (verify.py, verify_structured.py: a non-primary
  // row at exactly 0.6, source 'verifier') must not survive approval — with
  // it still attached, v_topics.question_count and v_mcq_questions.topic_codes
  // go on listing the question under a topic nobody chose. This runs whether
  // or not the reviewer overrides the topic: `assignTopic` below only ever
  // clears `is_primary` rows, and a plain approve touches question_topics not
  // at all otherwise. Scoped to `source = 'verifier'` specifically — never a
  // blanket delete of every non-primary row, since a genuine editorial
  // secondary topic (source 'model', written by the original tagger) has to
  // survive approval exactly as it is.
  // An approved question's crops go to the public bucket so students get them
  // from the CDN. Awaited (the serverless function may stop once it responds),
  // but a failure only slows the crop down: the image falls back to the gated
  // route. See lib/cropUrl.ts.
  if (decision === "approved" && publicCropsEnabled()) {
    await publishCrops(await cropKeysFor(client, group.map((row) => row.id)));
  }

  if (decision === "approved") {
    await client
      .from("question_topics")
      .delete()
      .in("question_id", group.map((row) => row.id))
      .eq("is_primary", false)
      .eq("source", "verifier");
  }

  let previousTopicSlug: string | null = null;
  let topicWarning: NextResponse | null = null;
  if (topicCode && decision === "approved") {
    const assigned = await assignTopic(client, questionId, topicCode);
    previousTopicSlug = assigned.previousTopicSlug;
    topicWarning = assigned.response;
  }

  // Revalidate regardless of `topicWarning`: the approve/reject decision
  // above already landed even when the topic override failed to save, and
  // the pages that reflect *that* must not stay stale just because this
  // request has something else to report.
  await revalidateForQuestion(
    client,
    questionId,
    previousTopicSlug ? [previousTopicSlug] : [],
  );

  if (topicWarning) return topicWarning;
  return NextResponse.json({ ok: true, questionId, decision });
}

/** The result of an `assignTopic` call: a response to return early with, if
 *  something needs saying, and — regardless of that — the slug of whatever
 *  topic held `is_primary` before this call, so the caller's revalidation
 *  can drop the question from a topic page that a fresh read can no longer
 *  see it belongs to. */
interface AssignTopicResult {
  response: NextResponse | null;
  previousTopicSlug: string | null;
}

/** Record the reviewer's own topic choice, replacing the classifier's.
 *
 *  `source = 'human'` is the point: it is what lets a later pass tell which
 *  tags were checked by a person and which the model produced, so retraining or
 *  re-tagging never overwrites a human decision. */
async function assignTopic(
  client: SupabaseClient,
  questionId: string,
  topicCode: string,
): Promise<AssignTopicResult> {
  const { data: topic } = await client
    .from("topics")
    .select("id,syllabus_versions!inner(is_current)")
    .eq("code", topicCode)
    .eq("syllabus_versions.is_current", true)
    .limit(1)
    .maybeSingle();

  if (!topic) {
    // The decision itself already landed; say what did not, rather than
    // reporting a failure that would make the reviewer redo the approval.
    return {
      response: NextResponse.json(
        { ok: true, questionId, warning: `No current topic with code ${topicCode}.` },
        { status: 200 },
      ),
      previousTopicSlug: null,
    };
  }

  // Read before overwriting: once `is_primary` is unset below, this is the
  // only place the previous topic can still be identified from.
  const { data: previous } = await client
    .from("question_topics")
    .select("topics(slug)")
    .eq("question_id", questionId)
    .eq("is_primary", true)
    .maybeSingle();
  // Same array-vs-object type mismatch as revalidateForQuestion's identical
  // embed, for the same reason: a single not-null FK, typed as a list only
  // because supabase-js has no generated schema to know better.
  const previousTopicSlug =
    (previous as unknown as { topics: { slug: string } | null } | null)?.topics?.slug ?? null;

  // The topic being replaced is removed, not demoted. Unsetting `is_primary`
  // and leaving the row behind turns every correction into a second topic the
  // question goes on being listed under — including, in the live data this was
  // found in, a reviewer's own earlier choice sitting alongside their later
  // one. (One primary per question is a unique index, so the old row has to go
  // before the upsert either way.)
  await client
    .from("question_topics")
    .delete()
    .eq("question_id", questionId)
    .eq("is_primary", true)
    .neq("topic_id", (topic as { id: string }).id);

  const { error } = await client.from("question_topics").upsert(
    {
      question_id: questionId,
      topic_id: (topic as { id: string }).id,
      confidence: 1,
      source: "human",
      is_primary: true,
    },
    { onConflict: "question_id,topic_id" },
  );

  if (error) {
    return {
      response: NextResponse.json(
        { ok: true, questionId, warning: `Topic not saved: ${error.message}` },
        { status: 200 },
      ),
      previousTopicSlug,
    };
  }
  return { response: null, previousTopicSlug };
}

interface RetagBody {
  paperSlug?: unknown;
  displayLabel?: unknown;
  topicCode?: unknown;
}

/** Correct a topic after the question has already been decided.
 *
 *  POST's own topic override only ever runs in the same request as an
 *  approval, and its UPDATE is deliberately scoped to `UNDECIDED` — a
 *  question already approved will not match it, and the reviewer sees "not
 *  awaiting a decision" with no way back in. That guard is right for the
 *  approve/reject decision itself: re-deciding whether to publish something
 *  should not happen by accident. But the topic is a separate judgement call
 *  that can turn out wrong once a reviewer has seen more of the bank, and
 *  fixing it should not require unpublishing the question first. PATCH is
 *  that second, narrower door: it only ever touches `question_topics`, never
 *  `extraction_status`, and works regardless of what the question's current
 *  decision is.
 *
 *  Addressed by paper slug and display label rather than a question id: those
 *  are what a reviewer actually has looking at a published question, and
 *  resolving them here means the review UI does not need to keep the id of
 *  something it already approved and stopped tracking. */
export async function PATCH(request: Request) {
  const auth = await requireReviewer();
  if (auth.refusal) return auth.refusal;

  const client = serviceClient();
  if (!client) {
    return NextResponse.json({ error: "No database configured." }, { status: 503 });
  }

  let body: RetagBody;
  try {
    body = (await request.json()) as RetagBody;
  } catch {
    return NextResponse.json({ error: "Expected JSON." }, { status: 400 });
  }

  const paperSlug = typeof body.paperSlug === "string" ? body.paperSlug.trim() : "";
  const displayLabel =
    typeof body.displayLabel === "string" ? body.displayLabel.trim() : "";
  const topicCode = typeof body.topicCode === "string" ? body.topicCode.trim() : "";

  if (!paperSlug || !displayLabel || !topicCode) {
    return NextResponse.json(
      { error: "paperSlug, displayLabel and topicCode are required." },
      { status: 400 },
    );
  }

  const { data: paper } = await client
    .from("papers")
    .select("id")
    .eq("slug", paperSlug)
    .maybeSingle();
  if (!paper) {
    return NextResponse.json({ error: `No paper "${paperSlug}".` }, { status: 404 });
  }

  const { data: question } = await client
    .from("questions")
    .select("id")
    .eq("paper_id", (paper as { id: string }).id)
    .eq("display_label", displayLabel)
    .maybeSingle();
  if (!question) {
    return NextResponse.json(
      { error: `No question ${displayLabel} on ${paperSlug}.` },
      { status: 404 },
    );
  }

  const questionId = (question as { id: string }).id;
  const assigned = await assignTopic(client, questionId, topicCode);
  await revalidateForQuestion(
    client,
    questionId,
    assigned.previousTopicSlug ? [assigned.previousTopicSlug] : [],
  );
  if (assigned.response) return assigned.response;
  return NextResponse.json({ ok: true, questionId, topicCode });
}

/** Undo: put a question back in the queue.
 *
 *  Restores 'needs_review' when the pipeline had flagged it and 'extracted'
 *  when it had not, so undoing returns the queue to its previous shape rather
 *  than flattening the distinction the reviewer sorts by. */
export async function DELETE(request: Request) {
  const auth = await requireReviewer();
  if (auth.refusal) return auth.refusal;

  const client = serviceClient();
  if (!client) {
    return NextResponse.json({ error: "No database configured." }, { status: 503 });
  }

  const { searchParams } = new URL(request.url);
  const questionId = searchParams.get("questionId") ?? "";
  if (!questionId) {
    return NextResponse.json({ error: "questionId is required." }, { status: 400 });
  }

  const group = await resolveGroup(client, questionId);
  if (!group) {
    return NextResponse.json({ error: "No such question." }, { status: 404 });
  }

  // Each row goes back to what its *own* flags say, not the top-level
  // question's — a structured question's container rows are never flagged
  // themselves, so restoring the whole group to one status picked from the
  // question alone would put an unmatched leaf back as 'extracted' and hide
  // it from the queue's flagged-first sort.
  const withFlags = group.filter((row) => row.reviewFlags.length > 0).map((row) => row.id);
  const withoutFlags = group.filter((row) => row.reviewFlags.length === 0).map((row) => row.id);

  const decided = ["approved", "rejected"];
  const [flagged, clean] = await Promise.all([
    withFlags.length
      ? client
          .from("questions")
          .update({ extraction_status: "needs_review", reviewed_at: null, reviewed_by: null })
          .in("id", withFlags)
          .in("extraction_status", decided)
      : Promise.resolve({ error: null }),
    withoutFlags.length
      ? client
          .from("questions")
          .update({ extraction_status: "extracted", reviewed_at: null, reviewed_by: null })
          .in("id", withoutFlags)
          .in("extraction_status", decided)
      : Promise.resolve({ error: null }),
  ]);

  const error = flagged.error ?? clean.error;
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  // No longer approved, so no longer public.
  if (publicCropsEnabled()) {
    await unpublishCrops(await cropKeysFor(client, group.map((row) => row.id)));
  }

  await revalidateForQuestion(client, questionId);

  return NextResponse.json({ ok: true, questionId });
}
