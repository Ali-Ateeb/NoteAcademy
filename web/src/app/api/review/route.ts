/**
 * Reviewer decisions, written to Postgres.
 *
 * This is the only write path in the application, and it is the one that
 * decides what students see: approving a question publishes it. So it is worth
 * being explicit about what protects it.
 *
 * There is no authentication in this app yet. Until there is, a write endpoint
 * reachable from the internet is a write endpoint anyone can use, so this one
 * requires a shared secret in `REVIEW_TOKEN` and refuses outright when that is
 * not set. Deploying without configuring it leaves the route disabled rather
 * than open — the failure mode of forgetting is a queue that will not save,
 * which someone notices, instead of a bank strangers can publish into, which
 * nobody notices.
 *
 * The token is a stopgap for a single operator, not a substitute for auth. It
 * is never embedded in the page: the reviewer enters it once and the browser
 * keeps it. Before /admin is served publicly it needs real accounts, and
 * `reviewed_by` needs to record which of them made the call.
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
import { tokenMatches } from "@/lib/reviewAuth";

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

function guard(request: Request): NextResponse | null {
  if (!process.env.REVIEW_TOKEN) {
    return NextResponse.json(
      { error: "Review writes are disabled: REVIEW_TOKEN is not set." },
      { status: 503 },
    );
  }
  if (!tokenMatches(request.headers.get("x-review-token"))) {
    return NextResponse.json({ error: "Not authorised." }, { status: 401 });
  }
  return null;
}

/** Is this token the right one? Used by the unlock box, so entering a wrong
 *  token fails there — where it can be corrected — instead of silently, later,
 *  on every decision the reviewer makes. */
export async function GET(request: Request) {
  const refusal = guard(request);
  if (refusal) return refusal;
  return NextResponse.json({ ok: true });
}

interface DecisionBody {
  questionId?: unknown;
  decision?: unknown;
  topicCode?: unknown;
}

export async function POST(request: Request) {
  const refusal = guard(request);
  if (refusal) return refusal;

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
    .update({ extraction_status: decision, reviewed_at: new Date().toISOString() })
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

  // One primary per question is a unique index, so the old one has to go first.
  await client
    .from("question_topics")
    .update({ is_primary: false })
    .eq("question_id", questionId);

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
  const refusal = guard(request);
  if (refusal) return refusal;

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
  const refusal = guard(request);
  if (refusal) return refusal;

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
          .update({ extraction_status: "needs_review", reviewed_at: null })
          .in("id", withFlags)
          .in("extraction_status", decided)
      : Promise.resolve({ error: null }),
    withoutFlags.length
      ? client
          .from("questions")
          .update({ extraction_status: "extracted", reviewed_at: null })
          .in("id", withoutFlags)
          .in("extraction_status", decided)
      : Promise.resolve({ error: null }),
  ]);

  const error = flagged.error ?? clean.error;
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  await revalidateForQuestion(client, questionId);

  return NextResponse.json({ ok: true, questionId });
}
