# What is built, and what comes next

## In the repo

- **Schema** — nine migrations, all applied and verified against Postgres 16 +
  pgvector. `db/smoke_test.sql` exercises ten invariants and rolls back.
- **Pipeline** — render, vision extraction, cross-check, crop, mark-scheme
  matching, topic tagging, embedding, loading, plus a cost estimator. 15 unit
  tests over the deterministic stages.
- **Web app** — landing, subject directory, subject hub, split-screen viewer,
  timed resumable MCQ arena, topical browser, untimed topic drills, dashboard,
  and the reviewer's queue at `/admin/review`. 30 statically generated pages;
  28 browser checks.

The app runs on seed fixtures, so everything above is exercisable without a
database, an API key, or a single real PDF.

## Next, in order

**0. Ingest one real paper end to end**, before anything else here. Every figure
in `docs/architecture.md` — the $1,400 backfill, the 200 dpi floor, the
cross-check hit rate — is an estimate until one real Physics 5054 Paper 1 and its
mark scheme have been through `render → extract → mcq-key`. Half a day, and it
will change the order of everything below it.

**1. Point the app at Postgres.** Every function in `web/src/lib/data/catalog.ts`
becomes a query. No page component changes — that is what the seam is for. Add
Supabase Auth and apply `0009_supabase_auth.sql`.

**2. Move attempts server-side.** `web/src/lib/attempts.ts` already mirrors the
`attempts` and `practice_sessions` tables, so this is a change of transport.
Migrate a signed-out student's local history into their account on first login
rather than discarding it.

**3. Ingest the rest of the first subject.** Physics 5054, MCQ papers first,
using the measurements from step 0. Wire `/admin/review` to the database as you
go — the queue UI already exists, it needs a real backing store.

**5. Object storage and the real viewer.** R2 or S3 behind short-lived signed
URLs, PDF.js in `SplitViewer`, question crops served from `question_assets`.

**6. The AI solver.** `match_question()` first, RAG only as fallback, quota
enforced through `consume_quota()`.

**7. Payments.** Manual verification queue, then a gateway once demand is proven.

## Known gaps

- The viewer renders placeholder panes; object storage is not wired up.
- Structured (non-MCQ) questions have no arena — by design, since their
  segmentation is the part that needs the review queue first.
- No auth. The app is single-user-per-browser until step 1.
- `topic_mastery` is a materialised view with no refresh schedule yet.
- The pipeline's model calls are not covered by tests; only the deterministic
  stages are. They should be exercised against a handful of real papers held as
  fixtures once real content exists.
