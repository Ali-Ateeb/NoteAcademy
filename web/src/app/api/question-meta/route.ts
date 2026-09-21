/**
 * Which subject and topics a handful of questions belong to — the lookup the
 * student dashboards need, for exactly the questions that student has
 * attempted.
 *
 * Public and unscoped to a user, like `/api/revision-queue`: the request
 * carries question ids and the answer is public catalogue metadata (a
 * question's subject and topic codes are on every topic page already). The
 * student's answers never leave the browser through this route.
 *
 * POST rather than GET only because a long history is a long id list.
 */

import { NextResponse } from "next/server";

import { getQuestionMetaByIds } from "@/lib/data/catalog";

// Comfortably above the size of any one subject's whole MCQ bank, so a
// student who has attempted everything in every subject still fits in one
// request, while an unbounded list cannot be used to build an unbounded query.
const MAX_IDS = 3000;

// Ids are opaque strings here; what counts as a well-formed one depends on the
// data source (uuids in the database, short slugs in the fixtures), so that
// judgement is the catalog's, not the route's. This only bounds their size.
const MAX_ID_LENGTH = 64;

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Body must be JSON." }, { status: 400 });
  }

  const raw = (body as { ids?: unknown } | null)?.ids;
  if (!Array.isArray(raw) || !raw.every((id) => typeof id === "string")) {
    return NextResponse.json({ error: "ids must be an array of strings." }, { status: 400 });
  }

  const ids = [...new Set(raw as string[])].filter((id) => id.length > 0 && id.length <= MAX_ID_LENGTH);
  if (ids.length > MAX_IDS) {
    return NextResponse.json({ error: `At most ${MAX_IDS} ids per request.` }, { status: 413 });
  }

  try {
    return NextResponse.json({ questions: await getQuestionMetaByIds(ids) });
  } catch (error) {
    return NextResponse.json({ error: (error as Error).message }, { status: 500 });
  }
}
