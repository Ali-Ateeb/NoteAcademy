/**
 * The review queue, paged and filtered — the client-side counterpart to
 * `/admin/review`'s own initial server-rendered fetch.
 *
 * The page itself renders the first page synchronously (fast first paint,
 * same as before); everything after that — a filter change, or the reviewer
 * working past the first `REVIEW_PAGE_SIZE` questions — comes from here
 * instead of a full page navigation, so the keyboard-only reviewing flow
 * ReviewQueue.tsx is built around never has to stop for a page load.
 *
 * Gated the same way the page is: a signed-in account with `is_reviewer =
 * true` (`currentReviewer()`, checked against the caller's own session
 * cookie). Unlike `/api/review`, there is nothing to write here, but the
 * *read* is exactly what that check exists to protect — an unapproved
 * question's text, mark scheme, correct option and crop — so it gets the
 * same gate, not a weaker one just because this route is new.
 */

import { NextResponse } from "next/server";

import { getReviewQueue, REVIEW_PAGE_SIZE } from "@/lib/data/catalog";
import type { ReviewFlag } from "@/lib/data/types";
import { currentReviewer } from "@/lib/reviewerAuth";

const VALID_FLAGS = new Set<ReviewFlag>([
  "low_tag_confidence",
  "text_layer_mismatch",
  "bbox_outside_page",
  "unmatched_mark_scheme",
  "ambiguous_mark_scheme",
  "no_text_layer",
]);

export async function GET(request: Request) {
  if (!(await currentReviewer())) {
    // Indistinguishable from any other failure: this is the same data
    // /admin/review itself refuses to fetch without a reviewer session, so
    // it gets the same non-committal refusal rather than a more specific one.
    return NextResponse.json({ error: "Not authorised." }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const subjectSlug = searchParams.get("subject") || undefined;
  const flagParam = searchParams.get("flag");
  const flag = flagParam && VALID_FLAGS.has(flagParam as ReviewFlag)
    ? (flagParam as ReviewFlag)
    : undefined;

  const offset = Math.max(0, Number(searchParams.get("offset")) || 0);
  const requestedLimit = Number(searchParams.get("limit")) || REVIEW_PAGE_SIZE;
  // Clamped, not trusted outright: this is reachable with nothing but the
  // cookie, and an arbitrary limit is an arbitrary amount of crop-signing
  // work per request.
  const limit = Math.min(Math.max(1, requestedLimit), REVIEW_PAGE_SIZE);

  try {
    const page = await getReviewQueue({ subjectSlug, flag }, offset, limit);
    return NextResponse.json(page);
  } catch (error) {
    return NextResponse.json({ error: (error as Error).message }, { status: 500 });
  }
}
