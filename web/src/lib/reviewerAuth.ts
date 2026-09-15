/**
 * Who is allowed to use the review queue — real Supabase Auth accounts now,
 * not a shared secret. Replaces `reviewAuth.ts`'s `REVIEW_TOKEN`/cookie
 * check: `/admin/review`, `/api/review` and `/api/review-queue` all call
 * `currentReviewer()` instead of comparing a token.
 *
 * A reviewer is any account with `profiles.is_reviewer = true`. Nothing in
 * this app can set that flag on itself — 0029_reviewer_accounts.sql's
 * trigger refuses the change unless it comes from outside the `anon`/
 * `authenticated` API roles — so granting access is a deliberate act by
 * whoever operates the database (`update profiles set is_reviewer = true
 * where email = '...'`), the same way `subjects.is_published` already is.
 *
 * This is still a single-operator model in spirit — there is no UI to
 * manage reviewers, only direct SQL — but every decision now has a real
 * account behind it (`questions.reviewed_by`), and access can be granted or
 * revoked per person instead of by whoever happens to hold one string.
 */

import { serverSupabase } from "./supabase/serverClient";

export interface Reviewer {
  id: string;
  email: string | null;
}

/** The signed-in user, if their account is allowed to review — null both
 *  when nobody is signed in and when they are signed in but not a reviewer,
 *  which callers that only care about "can I show the queue" can treat
 *  identically. Callers that need to tell those two apart (to point a
 *  visitor at sign-in versus telling them they lack access) use
 *  `reviewerStatus` instead. */
export async function currentReviewer(): Promise<Reviewer | null> {
  const status = await reviewerStatus();
  return status.kind === "reviewer" ? status.reviewer : null;
}

export type ReviewerStatus =
  | { kind: "signed-out" }
  | { kind: "not-a-reviewer"; email: string | null }
  | { kind: "reviewer"; reviewer: Reviewer };

export async function reviewerStatus(): Promise<ReviewerStatus> {
  const supabase = await serverSupabase();
  if (!supabase) return { kind: "signed-out" };

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { kind: "signed-out" };

  // Read as the signed-in user, not the service role: profiles_self_read
  // (0008) already lets a user see their own row, is_reviewer included —
  // RLS is row-level, not column-level — so there is no reason to reach for
  // a more privileged client just to check a flag on the caller's own
  // account.
  const { data: profile } = await supabase
    .from("profiles")
    .select("is_reviewer")
    .eq("id", user.id)
    .maybeSingle();

  if (!(profile as { is_reviewer: boolean } | null)?.is_reviewer) {
    return { kind: "not-a-reviewer", email: user.email ?? null };
  }
  return { kind: "reviewer", reviewer: { id: user.id, email: user.email ?? null } };
}
