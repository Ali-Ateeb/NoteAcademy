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
  const expected = process.env.REVIEW_TOKEN ?? "";
  if (!expected || !supplied || supplied.length !== expected.length) return false;
  let difference = 0;
  for (let i = 0; i < expected.length; i += 1) {
    difference |= expected.charCodeAt(i) ^ supplied.charCodeAt(i);
  }
  return difference === 0;
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

  // Only an undecided question can be decided. Without this a replayed request
  // could quietly re-approve something a reviewer had already rejected.
  const { data, error } = await client
    .from("questions")
    .update({ extraction_status: decision, reviewed_at: new Date().toISOString() })
    .eq("id", questionId)
    .in("extraction_status", UNDECIDED)
    .select("id");

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
  if (!data?.length) {
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

  const { data: question } = await client
    .from("questions")
    .select("review_flags")
    .eq("id", questionId)
    .maybeSingle();

  const flags = (question as { review_flags?: string[] } | null)?.review_flags ?? [];
  const restored = flags.length ? "needs_review" : "extracted";

  const { error } = await client
    .from("questions")
    .update({ extraction_status: restored, reviewed_at: null })
    .eq("id", questionId)
    .in("extraction_status", ["approved", "rejected"]);

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
  return NextResponse.json({ ok: true, questionId, restored });
}
