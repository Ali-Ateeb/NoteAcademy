# What is built, and what comes next

## In the repo

- **Schema** — nine migrations, all applied and verified against Postgres 16 +
  pgvector. `db/smoke_test.sql` exercises ten invariants and rolls back.
- **Pipeline** — render, vision extraction, cross-check, crop, mark-scheme
  matching, topic tagging, embedding, loading, plus a cost estimator. 15 unit
  tests over the deterministic stages.
- **Web app** — landing, subject directory, subject hub, split-screen viewer,
  timed resumable MCQ arena, topical browser, dashboard. 23 statically generated
  pages; 18 browser checks.

The app runs on seed fixtures, so everything above is exercisable without a
database, an API key, or a single real PDF.

## Next, in order

**1. Point the app at Postgres.** Every function in `web/src/lib/data/catalog.ts`
becomes a query. No page component changes — that is what the seam is for. Add
Supabase Auth and apply `0009_supabase_auth.sql`.

**2. Move attempts server-side.** `web/src/lib/attempts.ts` already mirrors the
`attempts` and `practice_sessions` tables, so this is a change of transport.
Migrate a signed-out student's local history into their account on first login
rather than discarding it.

**3. Ingest one real subject end to end.** Physics 5054, MCQ papers first. This
is where the real work is, and where the estimates in `docs/architecture.md`
should be replaced with measured numbers.

**4. Build the review queue.** An admin surface over
`extraction_status = 'needs_review'` and low-confidence `question_topics`. Not
optional tooling — it is the mechanism that keeps the bank correct, and it gates
everything downstream of it.

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
