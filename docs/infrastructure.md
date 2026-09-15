# Infrastructure and operations

How the system is actually put together, what happens when it runs, and where it
breaks under growth.

This is the operational companion to `architecture.md`, which explains *why* the
stack was chosen. This one explains *how it behaves* and *what it costs* — with
measurements taken against the live database rather than estimates. Anything
marked **measured** was run against the production Supabase project on
2026-09-15; anything marked **projected** is arithmetic on top of those numbers
and should be treated as an argument, not a fact.

**Status:** eight of the nine items on §9's fix list have since shipped
(everything except #7, the `question_options`/embedding backfill — still
open, still weeks of work, and still the reason the AI solver runs on its
fallback path rather than `match_question()`) —
`/admin/review`'s read path is now gated, the `also_in` index is applied,
`noteacademy audit-mcq-crops` catches the missing-option failure class this
document argues nothing else can, `.github/workflows/ci.yml` runs the checks
below on every push, the six content pages this document says are frozen at
build time (§2.1, §8.1) now carry a 15-minute ISR window plus on-demand
revalidation from `/api/review`, the review queue's 5,000-row silent
ceiling (§7.4) is gone — it pages and filters by subject and flag instead of
fetching everything at once — `REVIEW_TOKEN` (§4.2) is retired: both the
admin page and the write/read APIs now gate on a real Supabase Auth account
with `profiles.is_reviewer = true`, and `questions.reviewed_by` is finally
populated (`db/migrations/0029_reviewer_accounts.sql`) — and `/api/asset`
(§7.3) now downloads and returns a crop's bytes directly instead of
redirecting to a signed URL, cutting the common case from two browser round
trips to one and, because the response can now carry a `public`
Cache-Control (approved content is exactly what RLS already lets anyone
read), letting a CDN absorb every repeat view of a popular crop without
this function running at all. The rest of this document describes the state
that made those fixes necessary; it is left as written rather than edited
into the present tense throughout, because the reasoning is the point, not
the snapshot.

---

## 1. The shape of the system

Four components, two of which run continuously:

```
                    ┌──────────────────────────────────────────┐
   students ───────▶│  web/     Next.js 15 (React 19)          │
   crawlers ───────▶│           ~792 static pages + 4 routes    │
                    └────────────┬─────────────────────────────┘
                                 │ supabase-js (PostgREST, HTTPS)
                                 ▼
                    ┌──────────────────────────────────────────┐
                    │  Supabase                                │
                    │   • Postgres 15 + pgvector  (36 MB)      │
                    │   • Auth (GoTrue)                        │
                    │   • Storage — private bucket, 4,279 crops│
                    └────────────▲─────────────────────────────┘
                                 │ psycopg (direct 5432) + Storage REST
                    ┌────────────┴─────────────────────────────┐
   CAIE PDFs ──────▶│  pipeline/  Python batch jobs            │
   (466 files,      │             offline, no uptime, no port  │
    279 MB local)   └──────────────────────────────────────────┘
                    ┌──────────────────────────────────────────┐
                    │  db/       26 .sql migrations + apply.py │
                    └──────────────────────────────────────────┘
```

**Only `web/` and Supabase are online.** The pipeline is a CLI a human runs on a
laptop; it has no request path, no health check, and nothing depends on its
uptime. That is the single most consequential structural decision in the repo —
it means the thing most likely to be wrong (extraction) can never take the site
down, and the thing that must stay up (the site) has almost no moving parts.

There is **no CI, no Dockerfile, no `vercel.json`, no deployment manifest** in
the repository. Deployment today is `npm run build` against whatever host is
attached, and the verification gates (`typecheck`, `lint`, `build`, `e2e`) are
run by hand. See §8.6.

### Code inventory

| Area | Size | Notes |
|---|---|---|
| `pipeline/` | 7,172 lines, 23 modules | 170 unit tests, all passing |
| `web/src/` | ~3,300 lines components + ~1,500 lib | 28 browser checks (`e2e/smoke.mjs`) |
| `db/migrations/` | 26 files, 1,807 lines | applied via ledger, checksum-verified |
| `papers/` | 466 PDFs, 279 MB | local working set, not in git |

---

## 2. What actually happens on a request

### 2.1 A student opening a paper (the common case)

Nearly every student-facing page is **statically generated at build time**.
`/papers/[paper]`, `/practice/[paper]`, `/subjects/[subject]`,
`/topics/[subject]/[topic]`, `/topics/[subject]/[topic]/practice` and
`/dashboard/[subject]` all export `generateStaticParams`. None of them export
`revalidate`.

The consequence, stated plainly, because it drives most of §8:

> **There is no ISR and no on-demand revalidation. The entire question bank is
> frozen into the build output. Approving a question in the review queue changes
> the database and changes nothing a student can see until someone runs a new
> build and deploys it.**

So the request path for a paper page is: CDN edge → static HTML/RSC payload →
done. No database, no function invocation. That is genuinely excellent for
latency and cost, and it is why the site can serve crawlers cheaply — which
matters, because "5054 may june 2019 paper 12" is the acquisition channel.

The one dynamic element on those pages is the crop image (§2.3).

### 2.2 The four dynamic routes

| Route | Runtime | Auth | Purpose |
|---|---|---|---|
| `/api/asset/[...key]` | per-request | none (RLS-gated) | resolve storage key → signed URL |
| `/api/solve` | per-request | Supabase session + quota | LLM worked solution, cached in Postgres |
| `/api/review` | per-request | shared secret `REVIEW_TOKEN` | the only write path for content |
| `/admin/review` | `force-dynamic` | **none** | renders the review queue |
| `/auth/callback` | per-request | — | Supabase OAuth/magic-link exchange |

`middleware.ts` runs on every non-static request and calls
`supabase.auth.getUser()` to refresh the session cookie. It does **not** gate any
route — it is purely token refresh. Route protection does not exist.

### 2.3 How an image reaches the page

This is the most carefully-built part of the request path and worth following:

1. The static page carries a **stable, unsigned** path: `/api/asset/papers/5054/2019-mj/p11/crops/7.png`.
2. On click/render the browser hits that route.
3. The route calls `assetIsPublic(storageKey)`, which queries `question_assets`
   joined `questions!inner` **through the anonymous client** — so RLS decides.
   `questions_public` admits `extraction_status = 'approved'` only.
4. If visible, it mints a 1-hour signed URL with the service role and issues a
   302, `Cache-Control: private, max-age=1800`.

The design goal — never bake an expiring URL into a prerendered page — is met,
and the authorization check reuses the same policy that protects the question
rather than a parallel rule that could drift. A 404 is returned for both
"missing" and "not approved", so review state does not leak.

**Cost of this design:** every crop view is a Node function invocation plus two
network calls (one Postgres read, one Storage sign) before a single byte of
image moves. See §7.3.

### 2.4 Attempts: local-first, server-mirrored

`web/src/lib/attempts.ts` writes every attempt to `localStorage` first and
mirrors to Postgres best-effort when signed in. The local schema deliberately
mirrors the `attempts` table, including the `seq` monotonic counter — because
`created_at` is `now()` inside a transaction and cannot order rows written
together. On first sign-in, `migrateLocalAttempts` pushes the signed-out history
into the account, guarded by a per-(browser, account) flag so it runs once.

The dashboard is the only surface that switches source when signed in, reading
`current_answers` instead of the local log.

---

## 3. The data layer

### 3.1 Core shape

Three structural decisions that would be expensive to retrofit, and are correct:

- **Topics hang off a syllabus *version*, not a subject.** `topic_links` maps
  nodes across CAIE revisions with an explicit relation. Without it the topical
  engine silently rots as syllabuses change.
- **A paper is the sitting; documents hang off it** (`paper_documents`). Putting
  `doc_type` on `papers` would make a question paper and its mark scheme
  unrelated rows — and that join is exactly what the split-screen viewer needs.
- **`attempts` is append-only**, enforced at the database with triggers
  (`attempts_append_only_update`, `attempts_append_only_delete`; RULEs until
  0030, which found they made a profile with any attempts undeletable —
  cascade deletes rewrite through the same "do instead nothing" a direct
  write does), ordered by `seq bigserial`. Every analytic is a view over it,
  so metrics can be recomputed rather than migrated.

Questions self-reference via `parent_question_id`, so `1(a)(ii)` rolls into
`1(a)` into `1`. This is why a structured "question" and a structured *row* are
different units — a distinction migrations 0022, 0023 and 0026 exist to fix in
the review queue, the paper count, and the practice arena respectively.

### 3.2 The read views

The web app never assembles joins. `catalog.ts` issues flat selects against
views defined in SQL:

| View | Purpose | Full-scan cost (**measured**) |
|---|---|---|
| `v_subjects` | subject directory | trivial |
| `v_topics` | topic tree + question counts | 4.2 ms (83 rows) |
| `v_papers` | paper index + doc list | 8.9 ms (229 rows) |
| `v_structured_questions` | structured arena | 158 ms (1,858 rows) |
| `v_mcq_questions` | MCQ arena + topic drills | **3,564 ms (1,240 rows)** ⚠ |
| `v_review_queue` | reviewer surface | 124 ms |
| `v_review_decided` | post-decision lookup | — |

That `v_mcq_questions` number is the single worst measurement in the system and
is dissected in §7.1.

Every view is declared `security_invoker = true`. This is load-bearing: a plain
Postgres view executes with its *owner's* rights, which would make each of these
a hole straight past RLS. Migration 0024 fixed exactly this bug on
`current_answers`, which had been shipping since 0005 and would have returned
every student's answers to any authenticated caller.

### 3.3 The recurring Supabase footgun

Three separate migrations (0013, 0016, 0019, 0021, 0024) exist to fix instances
of one root cause:

> Supabase's default privileges grant `anon` and `authenticated` access to new
> relations in `public` **as they are created**. Declining to `grant` is not the
> same as `revoke`.

This has bitten `v_review_queue`, `topic_mastery` (a materialized view, which
*cannot* carry RLS at all), `current_answers`, and the views recreated by
`drop view` + `create or replace` (which discards grants and lets the default
reapply). Each was caught by auditing the live database, not by reading the
migrations.

**This is a systemic risk, not five closed tickets.** Every future migration
that creates a relation in `public` reintroduces it. See §9.

---

## 4. The security model

### 4.1 What is right

- RLS on every user-owned and content table; `questions_public` admits only
  `extraction_status = 'approved'`, so an unreviewed question cannot leak into
  the arena or topical engine even if application code forgets to filter.
- The private bucket is never exposed; only short-lived signed URLs are handed
  out, and only after an RLS-backed check.
- The service role key is read through a `typeof window === "undefined"` guard
  and never prefixed `NEXT_PUBLIC_`.
- `consume_quota` increments-then-checks-then-refunds, so concurrent requests
  cannot over-spend a daily limit.
- Function `search_path` pinned on `consume_quota` and `match_question`.

### 4.2 What is not

**`/admin/review` has no authentication.** The page is `force-dynamic`, reads
the queue with the **service role**, and renders it to anyone who requests the
URL. `REVIEW_TOKEN` gates only the *write* endpoint (`/api/review`). The only
mitigation in place is `robots: { index: false }`, which is a request to
crawlers, not access control.

Concretely, an unauthenticated visitor to `/admin/review` today receives: every
unapproved question, its extracted text, its mark-scheme text, its correct
option, its proposed topics, and **working signed URLs for its crops** — which
are images of copyrighted CAIE material the review queue exists to keep
unpublished. The route's own source comments acknowledge the write path needs
real accounts "before /admin is served publicly"; the read path is already
public.

This is the highest-severity item in this document.

**Secondary items:**

- `REVIEW_TOKEN` is a single shared secret with no rotation, no per-user
  identity, and no audit trail. `questions.reviewed_by` exists and is never
  populated, so there is no record of who approved what.
- `question_assets_public` is `for select using (true)` — the asset *rows* are
  world-readable. The protection is that `assetIsPublic` joins `questions!inner`,
  so approval is enforced at query time. Correct today, but the table's own
  policy does not express the intent, and a future caller that forgets the join
  gets no warning.
- No rate limiting anywhere. `/api/asset` and `/api/solve` are both
  unauthenticated-reachable surfaces (solve requires a session, but sessions are
  free to create).

---

## 5. The ingestion pipeline

### 5.1 Two paths, and why the cheap one matters

```
MCQ papers        PDF ─▶ geometric segmentation ─▶ crop ─▶ load
(deterministic)   MS  ─▶ answer-grid text parse  ─┘
                  no model call, no API key, cannot hallucinate

Structured papers PDF ─▶ render ─▶ vision extract ─▶ cross-check ─▶ crop ─┐
(+ fallback)                                                              ├─▶ match ─▶ tag ─▶ embed ─▶ load
                  MS  ─▶ render ─▶ extract entries ──────────────────────┘
```

The MCQ path exploits the fact that CAIE lays multiple-choice papers on a fixed
grid — question number alone in a left gutter at x≈49.6, everything else
indented past x≈72 — so boundaries are a *geometric fact*. `segment.py` refuses a
paper whose geometry does not match rather than emitting silently wrong crops,
and `validate_regions` rejects a paper that yields 38 of 40 questions or a
duplicate number.

`segment_structured.py` later extended the same argument to structured papers
via fixed per-level left margins, and `boldness.py` recovers bold/regular
detection on newer papers where CAIE embeds every font under one meaningless
shared name ("AllAndNone") — by frequency-counting raw glyph ids, since the font
name and PyMuPDF's bold flag both carry zero signal.

**The strategic point:** the deterministic paths cost nothing per paper and
cannot invent a question. Model calls are reserved for what geometry genuinely
cannot do — reading option *text* out of scrambled reading order
(`mcq_options.py`), describing figures, and topic classification.

### 5.2 Idempotency and the loader

`ingest_mcq_paper` is re-runnable: `upsert_question` keys on
`(paper_id, display_label)`, and crops are `clear_assets` + `attach_asset`. One
guard is important — a re-run **cannot silently revoke an approval**:

```sql
extraction_status = case
  when questions.extraction_status = 'approved' then 'approved'
  else excluded.extraction_status
end
```

`review_flags`, by contrast, is overwritten unconditionally. So re-running a
paper recomputes its flags from scratch, which is correct for pipeline-derived
flags and would clobber a human-added one if that ever existed.

### 5.3 Human review is the real constraint

Both the README and `architecture.md` say it, and the data confirms it:
inference is cheap, review is not. `bulk-approve` exists precisely because
reviewing 1,240 questions one at a time does not scale — it approves MCQs whose
primary topic is confident and which carry no other flag.

The counterweight is that bulk approval is where silent defects hide. The
missing-4th-option crop bug (fixed 2026-09-10, `segment.py`) is the canonical
example: eight approved, published questions whose crops omitted option D
entirely — one of them the *correct answer* — and the defect was invisible to
every automated check because the bbox was geometrically valid, just too short.
It was found by scanning extracted crop text for option-letter tokens and
eyeballing the candidates.

---

## 6. Where the system actually is today

**All figures measured 2026-09-15 against the production database.**

### 6.1 Content

| | Physics 5054 | Chemistry 5070 | Biology 5090 | Total |
|---|---:|---:|---:|---:|
| Papers | 81 | 74 | 74 | **229** |
| Approved MCQ | 594 | 318 | 312 | **1,224** |
| Approved structured (all rows) | 4,668 | 4,577 | 2,791 | **12,036** |
| Approved + tagged displayable units | 1,261 | 890 | 839 | **2,990** |

- **13,888 question rows** total: 1,240 MCQ, 12,648 structured (1,858 top-level +
  10,790 children).
- **2,990 displayable units** (1,224 MCQ + 1,766 top-level structured), and
  **100% of them have a crop** in the bucket. The "real but invisible" problem
  from roadmap step 0 is solved.
- **215 topics**, 164 revisable. `mathematics-4024` exists, unpublished, 0 papers.
- 254 questions marked as cross-variant duplicates via `canonical_question_id`.
- Database: **36 MB** (`questions` 15 MB). Storage: 4,279 crop objects.

### 6.2 Conspicuous zeros

These are not bugs; they are unbuilt paths. But they change what the
architecture is currently *doing* versus what it is *designed* to do:

| Table | Rows | Consequence |
|---|---:|---|
| `profiles` | **0** | nobody has ever signed up |
| `attempts` | **0** | the dashboard, `topic_mastery` and `current_answers` have never run on real data |
| `question_embeddings` | **0** | `match_question()` — the solver's whole anti-hallucination design — returns nothing; the AI solver is running on the *fallback* path only |
| `question_options` | **0** | no MCQ has option text; the arena renders the crop image and four bare letter buttons |
| `question_solutions` | **0** | solution cache cold |

The `question_options` zero has a real user-facing effect: `v_mcq_questions`
returns `options: {}` for every question, `McqArena` renders option text only
when present, and MCQ `question_text` is `null` by design in the geometric
loader. So **the MCQ bank is currently images-only** — invisible to search, to
embedding, to screen readers, and to any student on a slow connection who does
not load the crop.

### 6.3 Tagging coverage is complete — read the denominator carefully

A naive count suggests a coverage crisis: 2,990 of 13,260 approved rows carry a
topic, or 22.5%. That number is misleading, and worth spelling out because it is
easy to alarm yourself with it.

**Measured**, broken down properly:

| Population | Approved | Tagged |
|---|---:|---:|
| MCQ | 1,224 | **1,224 (100%)** |
| Structured, top-level | 1,766 | **1,766 (100%)** |
| Structured, sub-parts | 10,270 | 0 — *by design* |

Topics attach to the **practice unit**, not to every row. A structured question's
sub-parts (`9(a)(ii)` and friends) inherit their context from the top-level
question the student actually practises and the reviewer actually signs off; the
review queue (0022), the paper count (0023) and the arena (0023) all draw that
same distinction.

So the topical engine has **complete coverage of everything it can serve**. The
real tagging risk is not coverage but *accuracy*, and that is measured
differently — the second-opinion pass over Physics 5054 agreed on 564 of 600
(94%) and pushed the 36 disagreements to the top of the queue rather than
burying them.

---

## 7. Bottlenecks

Ranked by measured severity.

### 7.1 `v_mcq_questions` is O(N²) — 3.5 s, 99% of it one subquery ⚠

**Measured.** A full read of `v_mcq_questions` takes **3,564 ms** server-side for
1,240 rows. Isolating the subqueries:

| Query | Exec time |
|---|---:|
| base rows + joins | 3.7 ms |
| \+ `topic_codes` subquery | 7.9 ms |
| \+ `crop_storage_key` subquery | 6.9 ms |
| \+ **`also_in` subquery** | **3,523 ms** |

The `also_in` correlated subquery (added in migration 0018 to show "this question
also appears in…") matches duplicate groups with:

```sql
where coalesce(q2.canonical_question_id, q2.id)
    = coalesce(q.canonical_question_id, q.id)
```

An expression on both sides that no index can serve. The query plan is explicit:

```
Seq Scan on questions q2  (actual time=1.807..2.869 rows=0 loops=1240)
  Filter: ((id <> q.id) AND (COALESCE(...) = COALESCE(...)))
  Rows Removed by Filter: 13888
```

**1,240 loops × 13,888 rows = ~17.2 million row examinations per view read**, to
produce `rows=0` for the ~80% of questions that have no duplicate at all.

**Verified fix.** A single expression index:

```sql
create index questions_canonical_group_idx
  on questions ((coalesce(canonical_question_id, id)));
```

Tested in a rolled-back transaction against production: **3,564 ms → 30.1 ms, a
118× improvement.** Nothing was persisted; this is a one-line migration waiting
to be written.

Per-page today the filter does push down, so the damage is bounded:

| Page query | Exec |
|---|---:|
| one MCQ paper (arena) | 117 ms |
| one topic drill | 70 ms |
| one structured paper | 1.1 ms |

But 117 ms for 40 rows is still ~555k rows scanned, and it grows linearly with
the size of `questions` — which is the scaling trap in §8.1.

### 7.2 Build time is the deployment bottleneck

~792 static pages today, each doing its own data fetches, plus
`generateStaticParams` calls that themselves query. Because there is no ISR,
**every content change requires a full rebuild of every page**, and build time is
the floor on how fast an approved question reaches a student.

Today that is tolerable. §8.1 shows why it is the first thing to break.

### 7.3 `/api/asset` is two network round trips per image

Every crop view costs a function invocation → Postgres read (`assetIsPublic`) →
Storage sign → 302. A student working through a 40-question paper triggers 40 of
these. The 30-minute private cache helps repeat views but not first paint, and
the check is not memoized across requests.

`signedUrls()` (batched, 500 per call) already solves this for the review queue;
the student path has no equivalent because each page fetches images
independently from the browser.

### 7.4 The review queue loads up to 5,000 rows into one page

`getReviewQueue()` pages PostgREST in 1,000-row chunks up to `REVIEW_QUEUE_MAX =
5000`, then sorts in JS, then signs every crop (batched 500). `ReviewQueue.tsx`
is 924 lines and renders the whole list at once. The code's own comment
acknowledges this needs UI paging "for a backfill". With 620 rows currently
unapproved (335 `extracted`, 285 `needs_review`) it is fine; at 5,000 it will be
a multi-megabyte RSC payload and a slow, memory-hungry page. Past 5,000 it
silently truncates.

### 7.5 `v_structured_questions` uses an unindexable `LIKE`

```sql
where leaf.display_label like q.display_label || '(%'
```

Correlated per top-level question, with a `not exists` anti-join for
grandchildren. 158 ms for all 1,858 rows today — fine, and only 1.1 ms filtered
to one paper — but it is the same shape of problem as §7.1 and will degrade on
the same curve.

---

## 8. Scaling: what breaks, in what order

### 8.1 Content volume — the binding constraint

Current: 3 subjects, 229 papers, 13,888 rows. The stated ambition is a
ten-subject, sixteen-year backfill (and CAIE has ~40 subjects).

**Projected**, holding the current ratio (~76 papers and ~4,600 question rows per
subject):

| Subjects | Papers | Question rows | Static pages |
|---:|---:|---:|---:|
| 3 (today) | 229 | 13,888 | ~790 |
| 10 | ~760 | ~46,000 | ~2,600 |
| 40 | ~3,050 | ~185,000 | ~10,500 |

Three things break on that curve, in this order:

1. **`v_mcq_questions` (§7.1) becomes unusable before anything else.** Its cost
   is (rows returned) × (size of `questions`). At 40 subjects the inner scan is
   13× larger, so a single 40-question arena page goes from 117 ms to roughly
   **1.5 s**, and a full-bank read from 3.5 s to minutes. *Fix it before the
   next subject lands, not after* — it is one index (§7.1) and it is already
   verified.
2. **Build time.** ~10,500 pages, each with its own queries, several of which are
   the degraded ones above. A build that takes 10 minutes at 3 subjects does not
   take 33 at 40 — it takes longer, because per-page query cost is itself rising.
   Combined with no ISR, this means the lag between "reviewer approves a
   question" and "a student can see it" becomes hours.
3. **Static generation stops being the right model.** Somewhere between 2,500 and
   10,000 pages, prerendering everything costs more than it saves. The fix is not
   exotic — add `revalidate` and let `dynamicParams` generate the long tail on
   demand — but it is a deliberate architectural change and the SEO argument for
   static pages needs to survive it. ISR does survive it; going fully dynamic
   would not.

### 8.2 Review throughput — the human constraint

620 questions are currently unapproved. At 40 subjects the review queue is a
backlog of tens of thousands of items, and §7.4's 5,000-row ceiling is a hard
wall with **silent truncation** beyond it.

The pressure this creates is the dangerous part. `bulk-approve` is the relief
valve, and the missing-option incident (§5.3) is what bulk approval looks like
when it is wrong: eight published questions, one of them unanswerable, invisible
to every automated check. **Scaling review means scaling *verification*, not just
approval throughput** — the second-opinion pass (`tag-verify-export` /
`tag-verify-apply`, which caught 36 disagreements in 600 questions) is the model
to extend, not the bulk approver.

Missing today, and needed before the queue grows: UI paging, per-subject and
per-flag filtering, real reviewer accounts, and `reviewed_by` actually populated
so decisions are attributable.

### 8.3 Traffic — genuinely the least of the problems

Static pages on a CDN absorb read traffic essentially for free, and the
architecture's claim that "Postgres handles the first 10k users" is sound,
because signed-out students hit the database **zero** times per page view. The
real per-user database load is:

- `/api/asset`: one read + one sign per crop (§7.3) — this is the volume driver
- `attempts` inserts: one row per answer, append-only
- `middleware`: one `getUser()` per non-static request

The first of those is the one that scales with engagement rather than with
signups. A student doing three 40-question papers generates ~120 asset
round trips and ~120 attempt rows. At 10,000 daily active students that is ~1.2M
function invocations/day for images alone — well beyond what a free/hobby tier
absorbs, and the first place the hosting bill becomes real.

Mitigation is straightforward and not yet done: longer signed-URL TTLs with
matching cache headers, or a signed-cookie/CDN-token scheme that removes the
per-image function invocation entirely.

### 8.4 Cost

| Driver | Current | At scale |
|---|---|---|
| Vision extraction | ~$1,400 for a 10-subject/16-year backfill (`noteacademy estimate`) | one-off per subject, not recurring |
| Embeddings | $0 — **none generated** | one-off, cheap, hash-gated against re-billing |
| AI solver | $0 — cache cold | **the recurring one** |
| Storage | 4,279 objects | linear, cheap |
| Function invocations | negligible | §8.3 |

The solver is designed well against cost: `question_solutions` caches one
solution per question forever, so cost scales with *bank size*, not usage, and
`consume_quota` caps free-tier users at 5/day. The exposure is that
`FREE_DAILY_LIMIT` is a flat constant with no `plan_tier` lookup yet, and quota
is per-account while accounts are free to create.

### 8.5 The retrieval index is unbuilt

`question_embeddings` is empty, so `match_question()` never fires and the
solver's central design — *identify* the question and inject its verbatim mark
scheme rather than doing RAG — is not running. Every solve today takes the
fallback path, which is exactly the path `architecture.md` says hallucinates
more. Two coupled problems make this non-trivial to fix:

- MCQs have `question_text = null` and no option text, so **there is nothing to
  embed** for 1,224 of them until `mcq-options` backfills them.
- The dimensionality (1024, voyage-3) is baked into the column type. Changing
  the model means a migration plus a full re-embed.

### 8.6 Operations and deployment

The gaps here are structural rather than performance-related:

- **No CI.** Four verification gates exist (`typecheck`, `lint`, `build`, `e2e`)
  and nothing enforces them. The pipeline's 170 tests likewise run by hand.
- **No migration rollback.** `db/apply.py` is a forward-only ledger with
  checksum verification — good for integrity, but a bad migration is fixed by
  writing another one, in production, by hand.
- **No backup or restore procedure documented.** The database is 36 MB and
  trivially dumpable; the crop bucket is the harder half, and both are
  reproducible from `papers/` + the pipeline, which is a real asset — but only
  if that reproduction path is ever tested.
- **No observability.** No error tracking, no query monitoring, no alerting. §7.1
  was a 3.5-second query sitting in the hot path of the main feature, and nothing
  in the system would ever have reported it.
- **Single-operator assumptions throughout**: one shared `REVIEW_TOKEN`, one
  `physics-5054` default in `getReviewTopicOptions`, manual publish via
  `update subjects set is_published = true`.

### 8.7 Correctness risks that scale silently

The defects most damaging to this product do not throw. They publish.

- The missing-option bug produced geometrically valid, fully approved,
  student-visible crops that omitted an answer. Caught by a manual audit.
- The `also_in` regression is a 3.5 s query that returns *correct* results.
- Migrations 0013/0016/0024 each closed a data-exposure hole found by auditing
  the live database, not by reading code.

The common thread: **this system has strong gates against invalid data and
almost no detection for plausible-but-wrong data.** As the bank grows, the ratio
of "reviewed by a human who was paying attention" to "approved in bulk" moves in
the wrong direction. Automated invariant checks over the *output* — every
approved MCQ crop contains four option labels; every approved question has a
crop that resolves; no bbox is shorter than its font size implies — are the
cheapest available insurance and do not exist yet.

---

## 9. What I would fix first

Ordered by (damage avoided) ÷ (effort).

| # | Action | Effort | Why now |
|---|---|---|---|
| 1 | **Put auth in front of `/admin/review`** | hours | Unapproved copyrighted content is publicly readable today (§4.2). Nothing else on this list is a live exposure. |
| 2 | **Add the `coalesce(canonical_question_id, id)` index** | one line | Verified 3,564 ms → 30 ms (§7.1). Blocks subject #4 from making the arena slow. |
| 3 | **Add output invariant checks to the pipeline** | days | The only defence against the §8.7 failure class. Start with "every approved MCQ crop shows four option labels". |
| 4 | **CI running the four existing gates + 170 tests** | hours | The checks already exist; nothing runs them. |
| 5 | **ISR (`revalidate`) + on-demand revalidation on approve** | days | Breaks the "approval requires a full rebuild" coupling before it becomes hours-long (§8.1). |
| 6 | **Paging + filtering in the review queue** | days | 5,000-row silent truncation is a wall, and review is the throughput constraint (§8.2). |
| 7 | **Backfill `question_options` / `question_text`, then embed** | weeks | Unblocks search, accessibility, and the solver's whole anti-hallucination design (§8.5). |
| 8 | **Populate `reviewed_by`; retire the shared token** | days | Attribution for every publish decision. |
| 9 | **Reduce `/api/asset` to one round trip** | days | The cost driver that scales with engagement (§8.3). |

### A note on what not to change

The instincts in this codebase that are worth defending under scaling pressure:

- **Deterministic-first extraction.** Geometry that refuses beats a model that
  guesses. Extend it (as `segment_structured.py` and `boldness.py` already did)
  rather than reaching for the vision path.
- **RLS as the gate, not application code.** Every leak found so far was a
  *bypass* of this model (matviews, owner-rights views, default grants) — never a
  failure of the model itself.
- **Static-by-default rendering.** The answer to §8.1 is ISR, not dynamic
  rendering. The SEO surface is the acquisition channel.
- **Crops as the display artifact, bbox stored alongside.** This is what made the
  missing-option fix a re-render rather than a re-extraction, and what makes the
  whole bucket reproducible from `papers/`.
