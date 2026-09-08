# What is built, and what comes next

## In the repo

- **Schema** — seventeen migrations, applied to the live Supabase project and
  verified there. `db/smoke_test.sql` exercises thirteen invariants and rolls
  back; all thirteen pass against the hosted database, with real content in it.
- **Pipeline** — render, vision extraction, cross-check, crop, mark-scheme
  matching, topic tagging, embedding, loading, deterministic multiple-choice
  ingestion, syllabus parsing, plus a cost estimator. 77 unit tests over the
  deterministic stages.
- **Web app** — landing, subject directory, subject hub, split-screen viewer,
  timed resumable MCQ arena, topical browser, untimed topic drills, dashboard,
  and the reviewer's queue at `/admin/review`. 28 browser checks.

The app runs on seed fixtures with no database, no API key and no PDFs, and
reads Postgres when it is configured. It never mixes the two.

## Next, in order

**0. Make the ingested questions displayable.** Three real Physics 5054 Paper 1
sittings (2015, 2019, 2026) are in the database — 120 questions, 120/120 with an
answer, each with its crop box. What they do not have is anything to *show*: no
option text, because reading order in these papers is scrambled, and no crop
URL, because object storage is not wired up. Either one unblocks the arena, and
storage (step 5) is the cheaper and more honest of the two — the crop is the
question as printed. Until then the bank is real and invisible.

**0b. Review and publish.** 120 questions are sitting unapproved. Reviewing them
needs `SUPABASE_SERVICE_ROLE_KEY` in `web/.env.local` — it is the only
credential that can read an unapproved question — and publishing the subject is
a one-line update once its bank has been looked at:

```sql
update subjects set is_published = true where slug = 'physics-5054';
```

**1. ~~Point the app at Postgres.~~** Done. `web/src/lib/data/catalog.ts` reads
the views in `0012_read_views.sql` through supabase-js; no page component
changed. Supabase Auth itself is still to come — `0009_supabase_auth.sql` is
applied, but nothing signs in yet.

**2. Move attempts server-side.** `web/src/lib/attempts.ts` already mirrors the
`attempts` and `practice_sessions` tables, so this is a change of transport.
Migrate a signed-out student's local history into their account on first login
rather than discarding it.

**3. Ingest the rest of the first subject.** Physics 5054, every MCQ sitting,
through `noteacademy load-mcq`. Deterministic and free, and now a single command
per paper. `/admin/review` already reads the database when the service role key
is present.

**4. Topic tagging.** The closed list the classifier picks from is loaded:
Physics 5054 (2026-2028), parsed from the published syllabus by
`noteacademy load-syllabus` — 6 sections, 83 topics, 270 learning outcomes. This
is the first step that genuinely needs `ANTHROPIC_API_KEY`, and the one where
the review queue earns its keep: tagging accuracy *is* the product, and a
topical bank that is 80% right is worse than none.

Tag against the 63 nodes that carry outcomes, never the 20 containers —
`revisableTopics()` is that list.

**5. ~~Object storage~~ and the real viewer.** Storage is done: crops upload to
a private Supabase bucket as part of `load-mcq`, and the app serves them through
`/api/asset`, which signs a URL only for a crop whose question the anonymous
role can already see. What is left of this step is the *document* viewer —
PDF.js in `SplitViewer` against the question papers themselves, which are
recorded in `paper_documents` but not yet uploaded.

**6. The AI solver.** `match_question()` first, RAG only as fallback, quota
enforced through `consume_quota()`.

**7. Payments.** Manual verification queue, then a gateway once demand is proven.

## Known gaps

- The split viewer still renders placeholder panes: question crops are served,
  the source PDFs are not uploaded yet.
- No authentication. `/admin/review` is reachable by anyone who can reach the
  app, and its write endpoint is protected only by `REVIEW_TOKEN` — a stopgap
  for a single operator. Real accounts are needed before /admin is public, and
  `reviewed_by` should record which one made the call.
- Structured (non-MCQ) questions have no arena — by design, since their
  segmentation is the part that needs the review queue first.
- No auth. The app is single-user-per-browser.
- Ingested questions carry no text and no option text, so they cannot be
  rendered yet. See step 0.
- The topic tree is loaded but rendered flat; the hierarchy is in the data and
  not yet in the UI.
- Nothing is tagged to a topic yet, so every topic reads as 0 questions.
- Reviewer decisions are written to Postgres, but `reviewed_by` is null: there
  is no identity to record yet.
- `topic_mastery` is a materialised view with no refresh schedule yet.
- The pipeline's model calls are not covered by tests; only the deterministic
  stages are. They should be exercised against a handful of real papers held as
  fixtures once real content exists.
