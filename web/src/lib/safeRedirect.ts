/**
 * `next` is attacker-controlled — it comes straight off the query string and
 * ends up in a redirect (`/auth/callback`) or a client-side navigation
 * (`/login`). This app never needs it to be anything but one of a small,
 * fixed set of same-origin paths (`/dashboard`, `/reset-password`,
 * `/admin/review`), so anything else is treated the same as if it were
 * absent.
 *
 * A bare `startsWith("/")` is not enough on its own:
 * - `"//evil.com"` is scheme-relative — a browser resolves it against a
 *   different host, not this one.
 * - `"/\\evil.com"` is normalised to the same scheme-relative shape by some
 *   browsers before navigation ever sees it.
 * - A `next` built by string concatenation rather than the History API (see
 *   `/auth/callback`) can also carry a raw CR/LF, which is a header-injection
 *   vector on the `Location` response header it becomes.
 *
 * Verified against the actual exploit this replaces: `next=@evil.com`
 * produced `Location: http://localhost:3220@evil.com`, which every browser
 * parses as host `evil.com` (everything before the last `@` is userinfo).
 * `@evil.com` fails the leading-`/` check outright, same as the scheme-
 * relative and CR/LF variants above.
 */
const UNSAFE_NEXT_PATTERN = /^\/\/|^\/\\|[\r\n]/;

export function safeNextPath(next: string | null | undefined, fallback: string): string {
  if (!next || !next.startsWith("/") || UNSAFE_NEXT_PATTERN.test(next)) {
    return fallback;
  }
  return next;
}
