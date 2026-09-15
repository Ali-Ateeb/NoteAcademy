# Infrastructure — second pass

Written 2026-09-15, after all nine items from `infrastructure.md`'s fix list
shipped (commits `58836e0`..`c69547f` on this branch). Two purposes: confirm
those fixes actually hold under fresh measurement rather than assume they do,
and look at surfaces the first audit didn't cover. Same rule as before:
**measured** means run against the live production database or a real build
in this session; anything else is marked as such.

---

## 1. The nine fixes, re-verified

| # | Fix | Re-check | Result |
|---|---|---|---|
| 1 | `/admin/review` auth | N/A — superseded by #8's real accounts | gate now `currentReviewer()`, not a cookie; confirmed no data fetch runs before it |
| 2 | `also_in` index | `explain analyze` on `v_mcq_questions` | Nested Loop / Hash Join, no seq-scan-per-row — **32ms server-side execution**, holding at 118×-ish faster than pre-fix |
| 3 | `audit-mcq-crops` | Re-ran across all 3 subjects | 1,224 checked, 0 suspects, ~10s runtime |
| 4 | CI | N/A this session | not independently re-run (would need a push to trigger); local `ruff`/`pytest`/`tsc`/`eslint` all clean |
| 5 | ISR + revalidation | N/A — not re-driven live this pass | code unchanged since verified |
| 6 | Review-queue paging | N/A — not re-driven live this pass | code unchanged since verified |
| 7 | *(not in original 9 — see §7 of `infrastructure.md`, still open, see §3 below)* | | |
| 8 | Reviewer accounts | Live DB check | `profiles`, `attempts` both still 0 rows; `questions.reviewed_by` still 0 non-null — clean |
| 9 | `/api/asset` caching | N/A — not re-driven live this pass | code unchanged since verified |

One measurement worth a correction to my own earlier report: I ran
`select * from v_mcq_questions` with no filter and got **925ms–1,350ms**
client-side, which on first look reads like a regression. It is not — `explain
analyze` on the identical query shows **32ms of actual Postgres execution**;
the rest is network transfer of ~590KB across the WAN link between this
sandbox and Supabase's US region, plus client-side row deserialization. The
web app never issues this unfiltered query — every real call site (`getPaper`,
`getQuestionsByTopic`, the review queue) filters by paper or subject first,
and those realistic queries measured 130–380ms end-to-end here, consistent
with the post-fix numbers already reported. Flagging this so the number
doesn't get mistaken for a live finding by a future reader skimming this file.

`db/apply.py --status` shows all 29 migrations applied with matching
checksums — no drift between what's on disk and what ran.

---

## 2. New finding: open redirect on `/auth/callback` (high)

`web/src/app/auth/callback/route.ts` builds the post-login redirect as:

```ts
const next = searchParams.get("next") ?? "/dashboard";
...
return NextResponse.redirect(`${origin}${next}`);
```

`next` is attacker-controlled (it's a query parameter) and never validated as
a same-origin path. A naive `next=https://evil.com` doesn't work — string
concatenation produces the malformed `http://localhost:3220https://evil.com` —
but a `next` starting with `@` does:

```
GET /auth/callback?code=<a valid code>&next=@evil.com
```

produces `Location: http://localhost:3220@evil.com`, which is the classic
userinfo-confusion redirect: everything before the last `@` is parsed as
discarded userinfo, and `evil.com` becomes the actual host. **Verified the
exact mechanism**, not just asserted it:

```js
> new URL("http://localhost:3220@evil.com")
{ host: "evil.com", href: "http://localhost:3220@evil.com/", username: "localhost" }
```

Any browser resolving that `Location` header navigates to `evil.com`, not
back to this app.

**Exploitability**: the attacker needs a *valid* `code` for the redirect
branch to run at all — but they can trivially get one by requesting their
own password reset or magic link, since `code` only proves *an* auth flow
completed, not that it belongs to the victim. So the attack is: request a
reset link for an attacker-controlled account, take the `code` out of it,
and send a victim `https://<real-domain>/auth/callback?code=<attacker's
code>&next=@evil.com`. The link's visible domain is genuinely this app's —
which is exactly what makes an open redirect on an *auth* endpoint worth
more to a phisher than one on an ordinary page: it's the one class of link a
user has been trained to trust with credentials-adjacent activity.

**Same shape, lower confidence, in `/login/page.tsx`**:
```ts
router.push((searchParams.get("next") ?? "/dashboard") as Route);
```
`router.push` drives client-side History API navigation, which browsers
generally refuse to take cross-origin, so this is likely not independently
exploitable the same way — but it takes the identical unvalidated input and
is one `router.push` implementation detail away from being so. Worth the
same fix for consistency, not just paranoia.

**Confirmed `next` is never meant to be anything but a small, app-controlled
set of values** — `forgot-password/page.tsx` sets it to the literal
`/reset-password`; nothing in this codebase ever needs it to be a full URL.
The fix is a same-origin check (`next.startsWith("/") && !next.startsWith("//")`,
rejecting a leading backslash too) before either callback route or `/login`
uses it, falling back to `/dashboard` on anything that fails it.

---

## 3. New finding: `/api/solve` can burn a quota unit for nothing (medium)

`web/src/app/api/solve/route.ts`'s only error handling is
`request.json().catch(() => null)` for the request body. The call that
actually costs money and quota has none:

```ts
const { data: withinQuota } = await service.rpc("consume_quota", {...});
if (!withinQuota) { ...429... }
...
const response = await genai.models.generateContent({...});   // <- unguarded
```

If `generateContent` throws — a Gemini rate limit, a transient network
error, a safety-filter rejection surfaced as an error rather than an empty
response — the quota unit consumed one line above is never refunded, and
the route returns no JSON body at all: Next.js's own generic error handling
takes over. Confirmed the client side compounds this rather than masking
it: `SolutionButton.tsx` does `const body = await res.json()` unconditionally
before checking `res.ok`; a non-JSON 500 body makes that call itself throw,
which the surrounding `try/catch` turns into `"Could not reach the solver."`
— a message that is actively wrong (the solver *was* reached; it failed
after already being charged for) and gives a student no indication they just
lost one of five daily free attempts for nothing.

Fix is a `try { ... } catch { ... }` around the `generateContent` call,
returning the route's own `{ error: ... }` shape, plus (the actually
important part) a compensating decrement of the quota counter on that path
— `consume_quota` already supports a negative `p_amount` for exactly this.

---

## 4. Confirmed non-issues, checked so they don't need re-checking

- **A cached solution can't go stale from an approved→unapproved
  transition**: `question_solutions`' RLS policy admits a solution only
  while its question is `approved`, and `/api/review`'s POST only allows
  `extracted`/`needs_review` → `approved`/`rejected` — there is no code path
  that moves an already-approved question back out of that state. The
  `/api/asset` public-cache change (fix #9) rests on the same fact and is
  safe for the same reason: nothing in this app currently un-approves
  published content, so there's no "stale public cache serves something
  that should no longer be visible" scenario to worry about *yet*. Worth
  re-checking if that ever changes (e.g. a future moderation-report flow).
- **`consume_quota` is correctly atomic under concurrency** — checked its
  SQL (0006): the upsert's `used = usage_counters.used + p_amount` is one
  statement, so two simultaneous requests from the same user are correctly
  serialized by Postgres itself. The *quota* accounting is not the gap in
  §3; the *missing refund on downstream failure* is.
- **Prompt injection surface in `/api/solve`'s `buildPrompt`**: all
  interpolated fields (`question_text`, `mark_scheme_text`,
  `examiner_comment`) come from the database, populated by the pipeline
  from PDF extraction — a student supplies only a `questionId`, looked up
  server-side. Not a user-controlled-prompt vector.
- **No other unvalidated redirect in the app** — `grep`ed every
  `NextResponse.redirect` and `router.push` call site; only the two named
  above ever consume a query-string-derived value. Everywhere else pushes a
  hardcoded literal.
- **New reviewer trigger doesn't interact badly with signup**: a fresh
  `profiles` row from `handle_new_user()` (0009) never sets `is_reviewer`
  explicitly, so it correctly defaults to `false` — a new signup can never
  accidentally land as a reviewer.

---

## 5. Not re-examined this pass

Scope was deliberately narrow — confirm the nine fixes, look hard at
previously-unexamined *security-sensitive* surfaces (auth flows, the solver).
Not covered here, and still exactly as `infrastructure.md` described them:
payments/billing (unbuilt beyond schema), the retrieval index (still zero
embeddings — item #7 from the original list, never picked up), the split
viewer's PDF.js integration, mobile/accessibility, and the pipeline modules
beyond `segment.py`/`crop_audit.py` (`markscheme.py`, `syllabus.py`,
`tagging.py`, `worksheet.py`, `boldness.py`, `extract.py`, `embed.py` were
skimmed for TODOs — none found — but not read line-by-line).
