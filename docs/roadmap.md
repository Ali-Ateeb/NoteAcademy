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

**4. ~~Topic tagging~~ for Physics 5054.** Done, and verified rather than taken on
faith: all 600 questions tagged against the 63 revisable nodes, then checked by
a second, independent pass that read the crops instead of the extracted text and
had the first pass's answer withheld (`tag-verify-export` / `tag-verify-apply`,
`pipeline/verify.py`). 564 of 600 (94%) agreed; the 36 that did not are now the
top of the review queue rather than lost inside it, each carrying both proposed
topics. Neither pass needed `ANTHROPIC_API_KEY` — both were done in a Claude Code
session, which is the point of `worksheet.py` and `verify.py` existing as JSON
in/JSON out rather than API calls: the classifier is whoever is available.

Chemistry 5070 and Biology 5090 are ingested but their syllabuses are not loaded,
so they have no closed list to tag against yet — `noteacademy load-syllabus`
against their published PDFs is the remaining step, then the same two passes.

**4b. Clearing the review queue at scale.** Reviewing 1240 questions one at a
time does not scale once most of the queue does not need it: `noteacademy
bulk-approve` (`pipeline/verify.py`) approves every MCQ whose primary topic is
confident and carries no other review flag — the same population the second
pass already vetted — in one call, dry-run by default because approving
publishes to students. `noteacademy dedupe` marks the handful of MCQs CAIE
reuses verbatim across a sitting's variants (`questions.canonical_question_id`,
migration 0018); the topical browser and drills fold these to one card and
name the other papers a question also appears in, while the timed arena still
shows every paper's own questions untouched.

Neither command has been run for real yet — `bulk-approve` needs a deliberate
go-ahead since it is the thing that decides what a student sees.

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
- Chemistry 5070 and Biology 5090 have no syllabus loaded and therefore no
  topic tags; every topic for those two subjects reads as 0 questions until
  `load-syllabus` is run against their published PDFs.
- Reviewer decisions are written to Postgres, but `reviewed_by` is null: there
  is no identity to record yet.
- `topic_mastery` is a materialised view with no refresh schedule yet.
- The pipeline's model calls are not covered by tests; only the deterministic
  stages are. They should be exercised against a handful of real papers held as
  fixtures once real content exists.
