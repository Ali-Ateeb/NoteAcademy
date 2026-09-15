/**
 * Gate for *reading* /admin/review, not just writing to it.
 *
 * `/api/review` already required a token before it would save a decision, but
 * nothing ever stopped the review queue's Server Component from fetching and
 * rendering the queue itself first — every unapproved question's text, mark
 * scheme, correct option and a working signed crop URL, to anyone who
 * requested the page. This route is what /admin/review checks before it
 * fetches anything at all: no cookie, no query.
 *
 * httpOnly, unlike the write token (kept in the browser's own localStorage so
 * `@/lib/review` can attach it to each save). Page script never needs to read
 * this one back, so it never gets the chance to.
 */

import { NextResponse } from "next/server";

import { ADMIN_COOKIE, reviewTokenConfigured, tokenMatches } from "@/lib/reviewAuth";

/** Long enough that a reviewer signs in once and keeps working across
 *  sessions; short enough that a stolen cookie does not stand forever. */
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 14;

export async function POST(request: Request) {
  if (!reviewTokenConfigured()) {
    return NextResponse.json(
      { error: "The review queue isn't configured: REVIEW_TOKEN is not set." },
      { status: 503 },
    );
  }

  const body = await request.json().catch(() => null);
  const token = typeof body?.token === "string" ? body.token : null;
  if (!tokenMatches(token)) {
    return NextResponse.json({ error: "That token was not accepted." }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(ADMIN_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: COOKIE_MAX_AGE_SECONDS,
  });
  return response;
}

/** Sign out of the review queue's read gate. Not load-bearing for security —
 *  the cookie expiring does the same job eventually — but a reviewer sharing
 *  a machine should be able to end the session on purpose. */
export async function DELETE() {
  const response = NextResponse.json({ ok: true });
  response.cookies.delete(ADMIN_COOKIE);
  return response;
}
