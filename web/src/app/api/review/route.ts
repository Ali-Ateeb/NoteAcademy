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
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

import type { ReviewDecision } from "@/lib/data/types";

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

/** Constant-time-ish comparison. The token is short and the endpoint is not a
 *  realistic timing-attack target, but there is no reason to make it one. */
function tokenMatches(supplied: string | null): boolean {
  // Trimmed on both sides. A .env written on Windows keeps CRLF, and a token
  // that silently carries a trailing carriage return would reject every
  // correct paste forever, with no way to tell that from a wrong token.
  const expected = (process.env.REVIEW_TOKEN ?? "").trim();
  supplied = supplied?.trim() ?? null;
  if (!expected || !supplied || supplied.length !== expected.length) return false;
  let difference = 0;
  for (let i = 0; i < expected.length; i += 1) {
    difference |= expected.charCodeAt(i) ^ supplied.charCodeAt(i);
  }
  return difference === 0;
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

  if (topicCode && decision === "approved") {
    const assigned = await assignTopic(client, questionId, topicCode);
    if (assigned) return assigned;
  }

  return NextResponse.json({ ok: true, questionId, decision });
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
): Promise<NextResponse | null> {
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
    return NextResponse.json(
      { ok: true, questionId, warning: `No current topic with code ${topicCode}.` },
      { status: 200 },
    );
  }

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
    return NextResponse.json(
      { ok: true, questionId, warning: `Topic not saved: ${error.message}` },
      { status: 200 },
    );
  }
  return null;
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
  if (assigned) return assigned;
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
  return NextResponse.json({ ok: true, questionId });
}
