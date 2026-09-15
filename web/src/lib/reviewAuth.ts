/**
 * The one shared secret that gates the review surface — both the write path
 * (`/api/review`, checked per-request via the `x-review-token` header) and
 * the read path (`/admin/review`, checked once via an httpOnly cookie; see
 * `/api/admin-auth`). Pulled out of `/api/review/route.ts` so both call the
 * same comparison rather than two copies drifting apart.
 *
 * This is a stopgap for a single operator, not a substitute for real
 * accounts: there is no per-reviewer identity, so `questions.reviewed_by` is
 * never populated. `/admin` needs real accounts before it has more than one
 * trusted operator.
 *
 * Server-side only, the same way `storage.ts` is: `process.env.REVIEW_TOKEN`
 * carries no `NEXT_PUBLIC_` prefix and must never reach a browser bundle.
 */

/** Name of the cookie that gates *reading* the review queue. Separate from
 *  the write token's storage (the browser's own localStorage, read by
 *  `@/lib/review`) because it protects a different thing: this cookie is
 *  checked by a Server Component before it fetches anything, so an
 *  unauthorised request never causes an unapproved question's text, mark
 *  scheme or crop to be read at all — let alone sent to a browser. httpOnly
 *  so page script can never read it back, unlike the write token today. */
export const ADMIN_COOKIE = "na-admin-token";

/** Constant-time-ish comparison. The token is short and the endpoint is not a
 *  realistic timing-attack target, but there is no reason to make it one. */
export function tokenMatches(supplied: string | null | undefined): boolean {
  // Trimmed on both sides. A .env written on Windows keeps CRLF, and a token
  // that silently carries a trailing carriage return would reject every
  // correct paste forever, with no way to tell that from a wrong token.
  const expected = (process.env.REVIEW_TOKEN ?? "").trim();
  const actual = supplied?.trim() ?? null;
  if (!expected || !actual || actual.length !== expected.length) return false;
  let difference = 0;
  for (let i = 0; i < expected.length; i += 1) {
    difference |= expected.charCodeAt(i) ^ actual.charCodeAt(i);
  }
  return difference === 0;
}

export function reviewTokenConfigured(): boolean {
  return Boolean((process.env.REVIEW_TOKEN ?? "").trim());
}
