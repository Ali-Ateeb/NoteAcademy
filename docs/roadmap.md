# Where the app stands

Rewritten 2026-09-15 (corrected and extended 2026-09-21: revision queue,
marks-weighted topics, the Voyage blocker and the Note Academy redesign) — the previous version of this file described a state
(120 questions, no auth, chemistry/biology "not loaded," no structured arena)
that everything shipped since had already overtaken without anyone updating
the doc. Everything below is checked against the live database and the actual
code as of this session, not carried forward from memory — the same rule
`docs/infrastructure.md` holds itself to. This file is being kept current for
the rest of this session; treat anything you read here as accurate for
whichever commit is checked out when you read it, and stale the moment real
work lands without a matching edit here, same as last time.

---

## Live today

**Content.** Three subjects published and playable, one registered but empty:

| Subject | Published | Approved MCQs | Approved structured | Papers with approved content |
|---|---|---:|---:|---:|
| Physics 5054 | yes | 1,802 | 669 | 121 |
| Chemistry 5070 | yes | 1,474 | 583 | 108 |
| Biology 5090 | yes | 265 | 534 | 74 |
| Mathematics 4024 | no | 0 | 0 | 0 |

Measured 2026-09-23, after the bulk-approve, dedupe re-run and review-queue
pass below. **MCQs are counted deduplicated** (`canonical_question_id is
null`) — the number a student can actually meet, since the topical browser
folds cross-paper repeats. Structured is top-level questions, not leaf parts.
Every revisable topic in physics (63) and chemistry (49) has at least one
approved MCQ; biology is 47 of 52, the gap entirely on the one paper left
outside the current 2016-2026 scope.

All three published subjects have a current 2026–2028 syllabus loaded, which
is what topic tagging and the topical browser are tagged against.

**Student-facing:**
- Subject directory, per-subject paper index by year, and a topic tree
  (headings as text, only revisable units as links) — all statically
  generated (ISR, 15 min) for search visibility.
- Split viewer: question paper beside mark scheme/examiner report,
  synchronised scroll, draggable split, keyboard shortcuts. The interaction
  shell is real; the panes are still placeholders — no source PDFs are
  uploaded yet (`paper_documents` has 458 rows of metadata, no files behind
  them).
- Timed, resumable MCQ arena (`McqArena.tsx`) — sit a real Paper 1 under exam
  timing, marked instantly; an examiner's note is shown after answering when the data has one (none does yet).
- Self-marked structured practice (`StructuredArena.tsx`) — untimed; the
  student marks their own handwritten answer against the real mark scheme.
  This did not exist when this file was last accurate.
- Topical drills — every question on one syllabus outcome, across every
  year, untimed. Verbatim MCQ duplicates across paper variants are folded to
  one card.
- Dashboard — accuracy per topic, worst first, from `localStorage` (signed
  out) or `current_answers` (signed in, cross-device via RLS).
- **Revise your weakest topics** (`/dashboard/[subject]/revise`, committed) —
  a drill built from the topics the student is actually getting wrong (>=3
  attempts, <70% accuracy, worst five), missed questions first then unseen
  ones, capped at 20. Public `/api/revision-queue` feeds it.
- **Marks-weighted topics** (`0031_topic_marks.sql`, applied) — each topic
  carries the total marks it has been worth across every past paper, and the
  subject page leads with "Where the marks actually are".
- Auth — real Supabase email/password accounts: sign-up (with email
  confirmation if the project requires it), sign-in, forgot/reset password.
  `next` (the post-login redirect target) is validated same-origin
  (`safeRedirect.ts`) before either `/auth/callback` or `/login` uses it —
  this closed a real open-redirect finding from the second-pass audit.
- AI solver (`SolutionButton` / `/api/solve`) — signed-in only, metered by a
  daily quota (`consume_quota`). Currently answers from its fallback path
  only; `match_question()` retrieval is unreachable until embeddings exist
  (see Known gaps).

**Review / admin:**
- `/admin/review` is gated by a real Supabase Auth account with
  `profiles.is_reviewer = true` — not a shared token. `questions.reviewed_by`
  records which account made each call. Paged (200/page, flagged and
  low-confidence items first), filterable by subject and flag.
- `noteacademy bulk-approve` / `bulk-approve-structured` — approve everything
  a confident, unflagged tag or a fully machine-vetted subtree already
  clears, dry-run by default.
- `noteacademy dedupe` — marks verbatim-duplicate MCQs across a subject's
  paper variants (`canonical_question_id`).
- `noteacademy audit-mcq-crops` — re-derives every MCQ's crop region from its
  source PDF and flags any stored crop that hides an option; wired into CI.

**Pipeline, current state:**
- MCQ and structured ingestion are both fully deterministic — geometric
  segmentation plus mark-scheme text matching, no model call, cannot
  hallucinate a question. This is a step beyond what was true when this file
  was last accurate (structured papers used to need a vision pass).
- Vision extraction (`extract.py`) is now a fallback path only, used for
  non-conforming or scanned papers and for the `mcq-options` backfill. It
  skips the model call outright for a page it can confidently tell is a
  cover, instructions, a formula sheet, or blank filler.
- Both `extract.py` (vision) and `tagging.py` (direct-API tagging, plus
  `tag_from_crop` for automated verification — see below) can run against
  **DeepSeek** instead of Gemini, switched independently per stage by
  `NOTEACADEMY_EXTRACTION_PROVIDER` / `NOTEACADEMY_TAGGING_PROVIDER`
  (`deepseek.py`, replacing `modelscope.py`; an unrecognised provider value is
  now an error rather than a silent fall-back to Gemini). **Live-tested
  2026-09-21 against the real API on two chemistry-5070 2020 papers, read-only,
  ~$0.06 in total; every call succeeded (109 of 109 HTTP 200, no retries
  needed).** Vision verification (`deepseek-flash`, `tag_from_crop`): 80/80
  MCQs, 90% agreement with the tags on file, 0 invalid topic codes, ~1.6s per
  call, 90% of input tokens served from DeepSeek's cache. Judged by eye on the
  crops, the 8 disagreements were: 1 clear DeepSeek error (an alloy conducting
  solid and molten -> it said ionic bonding, file's metallic bonding is right),
  1 where DeepSeek is arguably right (a question purely about which apparatus
  to use, filed under its rate-of-reaction context), and 6 genuinely
  multi-topic questions. **Thinking mode fixed both cases where the file was
  right** (the alloy question, and a saturated/unsaturated one) at 2-7s and
  ~600 output tokens per call (about $0.001) — so turn it on for verification
  runs: `NOTEACADEMY_DEEPSEEK_THINKING=1`. Text tagging (`deepseek-v4-pro`):
  12/12 calls worked, 7/12 agree with file; all 5 differences were questions
  the file itself had tagged at <=0.65 confidence, and its reasoning cites real
  syllabus objectives — but **its self-reported confidence is uniformly high
  (0.90-0.95), so it will not flag borderline questions for review the way
  Gemini's lower confidences did**. Page extraction (`deepseek-flash`): found
  every question label (14/14 MCQ, structured parts and marks plausible) but
  **its bounding boxes are loose — mean IoU 0.71 against the stored geometric
  boxes, only 5/14 at 0.8 or better, some 20-50pt off** — fine for reading
  text and options (the `mcq-options` backfill), not for cutting crops. A wrong
  key returns 401 in ~1.6s as `DeepSeekAccountError`, confirmed live. Design
  points worth knowing:
  `deepseek-flash` is the only vision model and `deepseek-v4-pro` (the default
  for text tagging) refuses images before any request is sent; thinking mode
  is on by default at DeepSeek's API and is explicitly turned off here
  (`NOTEACADEMY_DEEPSEEK_THINKING`) because these stages run sequentially; a
  401 or 402 aborts `tag-verify-auto` instead of being counted as thousands of
  failed questions; the syllabus leads each prompt so DeepSeek's automatic
  prefix cache hits. ModelScope/Qwen was tried earlier and found unreliable
  (persistent `504`s on vision calls with `Qwen3.8-Max`, `500`s with
  `Qwen3.7-Max`) — that code is in git history (before the DeepSeek switch) if
  ever wanted again.
- **Structured tag verification** (`tag-verify-structured`, `verify_structured.py`,
  built 2026-09-21): a vision model reads every page-crop of each structured
  question (1-4 pages, in order) and picks a primary plus up to two secondary
  topics; the result is compared with the first pass, which worked from text.
  Three outcomes, because a structured question usually tests several topics:
  *agreed* (same primary; confidence raised), *reordered* (different primary
  but the two reads share a topic; nothing written) and *disagreed* (nothing in
  common). **Safety rule: a disagreement on an already-approved question only
  lowers that tag's confidence and appears in the report — it never adds a tag
  or takes the question off the site**; on an unapproved one it returns it to
  the review queue as the MCQ verifier does. Every result is written to a
  triage CSV, disagreements first. Model calls run concurrently (`--workers`,
  default 8), and `--paper` limits a run to named papers. Dry run by default.
  Live-tested on 20 chemistry-5070 2020 questions, read-only. **Findings that
  change how to use it:** (1) thinking mode, which helped on MCQs, *hurts*
  here — 4x slower, verdicts changed on ~25% of questions between identical
  runs, and it over-weights the part with the most marks (it called an
  alcohols question "covalent bonding") — so this command defaults to thinking
  OFF; (2) even with it off, ~3 of 20 verdicts differ between identical runs,
  so one read is a flag for a human, not a ruling; (3) the one disagreement
  that held in all five runs (Chemistry 2020 MJ P22 Q1, filed as ionic bonds,
  read as identification of ions) is the kind worth a person's time. Measured
  cost: about $0.0004 a question with 98% of input served from cache, so all
  1,159 in-range structured questions come to roughly **$0.44 and ~16
  minutes**.
- **Year scope** (`NOTEACADEMY_YEAR_FROM/TO`, default 2016-2026;
  `--from-year/--to-year` per run): both automated verifiers read only that
  range. Papers outside it (2010-2015, 62 of 229) stay in the database and live
  on the site; only the model spend is skipped. Other commands (ingest,
  bulk-approve, embed) are not year-scoped.
- `mcq-options` (vision backfill of MCQ question/option text): 144
  `question_options` rows written (one paper, physics-5054's newest sitting,
  mostly done) before ModelScope's vision reliability problems stopped
  progress. 14 of 15 physics-5054 MCQ papers still untouched.
- `embed` (retrieval embeddings): built, tested live against real data,
  **never actually run** — `question_embeddings` is still 0 rows. **Blocked
  on the Voyage account, not on code:** with no payment method on file the key
  is held to 3 requests/min and 10K tokens/min, and a first real run hit
  persistent 429s. `embed.py` now retries with `Retry-After` and paces
  batches, but a run at that limit would take hours; adding a payment method
  lifts it. Earlier versions of this file said nothing was blocking it. That
  was wrong.
- 31 migrations, all applied to the live database with matching checksums
  (`db/apply.py --status`), including `0030` fixing the attempts/profiles
  cascade-delete bug.

---

## Paper 1 backfill, Physics and Chemistry 2016-2026 (2026-09-21)

Both subjects now have the complete Paper 1 (multiple-choice) grid for the
in-scope years: 21 sittings x variants 1 and 2 = **42 papers each**. Before,
Physics had 2 and Chemistry 8.

- **Source:** `ivyonline.co/past-papers/o-level/<code>`; files are public PDFs on
  `files.ivyonline.co/O/<code>/` named exactly as Cambridge names them
  (`5054_w22_qp_11.pdf`), so no renaming. 148 files, 36.9 MB, downloaded at
  ~2 requests/s, none overwritten, each verified as a real PDF with a matching
  mark scheme. The same site also serves **examiner reports and grade
  thresholds** — the two things missing from the data — not yet fetched.
  The site's terms of use were not reviewed.
- **Loaded:** 74 papers, **2,960 questions**, every one with its answer key and a
  crop in storage, except Chemistry 2017 M/J P11 Q25 (mark-scheme row did not
  match; flagged). Database is now 303 papers / 6,058 top-level questions.
  **All are unapproved and untagged** — invisible to students until they are
  tagged and reviewed.
- **Two segmenter bugs found and fixed** (`segment.py`), each from a real paper
  that the geometry method refused: a graph's origin label ("0") sitting in the
  question-number gutter (Physics 2021 O/N P11+P12), and a question opening
  with a single character so its number merged with it into one span
  ("37 Z ...", Chemistry 2018 M/J P11). Proven not to change any existing
  paper: all 104 previously-clean MCQ papers segment byte-for-byte identically,
  and the 3 failures now yield exactly 40 contiguous questions. Regression tests
  added; the bug-shaped ones fail on the old code.
- **Gotcha:** `load-mcq` only uploads crop images when given `--crops <dir>`;
  without it the database rows exist but storage is empty, which would show
  students broken images. Ran `fix-crops` (re-renders from the stored boxes)
  to upload all 2,960; audit is clean. Worth making the upload unconditional.
- Not done: Biology 5090 (not requested), 2010-2015 (out of scope), and the 8
  2010 variant-3 papers already on disk.

---

## Look and feel — Note Academy redesign (2026-09-21)

The site is being re-skinned to match the Note Academy brand and the reference
site (`Ali-Ateeb/nawa`, `toolbar` branch): a student's notebook rather than a
generic app. Light and dark are both done across the student-facing app;
committed in `6073199` (core pages) and the follow-up for the remaining pages.

Done:
- **Brand assets in place.** Cropped, WebP-optimised logos in
  `web/src/assets/brand/` (long lockup, stacked light/dark, pencil, and the
  calculator/compass/ruler stationery), all through one `Brand.tsx`.
  Favicon and Apple icon are the pencil; `opengraph-image.png` is the stacked
  logo on graph paper. The old `icon.svg` is gone.
- **Design system** (`globals.css`): dotted graph-paper ground drawn in CSS
  (no image), Raleway for display and body (variable, self-hosted via
  `next/font`) with Plex Mono kept for codes, marks and the timer; logo blue,
  periwinkle card, highlighter chips (`.hl`), yellow lined `.note-sheet`,
  pill buttons with the reference site's underline-then-glow hover (`.pill`).
- **Toolbar**: one floating bar — logo, Home/Subjects/Dashboard pills with an
  active state (`NavLinks.tsx`), account, theme, CTA. Fits at 375px.
- **Restyled pages**: landing (logo drop-in, bobbing stationery, periwinkle
  notes card), subjects index, subject page, topic page, dashboard, all four
  auth pages, and the MCQ arena including the results card.
- **No new runtime JS.** The reference site uses framer-motion and a Lottie
  splash; neither was brought over — every animation is CSS and honours
  `prefers-reduced-motion`. Shared JS is unchanged at 103 kB.

- **Dark theme** (2026-09-21): a midnight-blue notebook rather than neutral
  black — deep navy page and visible dots, chalk-coloured outlines with a blue
  hard shadow on the sticker cards and pills (`--edge` / `--pop` tokens),
  amber-tinted lined notes, dimmed highlighter chips, a darker periwinkle
  card. Both logos have real white colourways (no light plate); the stationery
  gets a thin die-cut outline so the dark calculator does not sink. The manual
  toggle overrides the OS setting in both directions (checked). Verified
  visually on landing, subject, topic, arena and results pages.

- **Remaining pages** (2026-09-21): `StructuredArena` (yellow sticky-note
  "mark yourself" panel, round mark chips, results card with the pencil),
  `SplitViewer` and the paper page (sticker-outlined viewer, pill tabs,
  highlighter labels), the revise-weak-topics flow, practice-page headings,
  and the admin review gate and primary buttons. Dark verified on the
  dashboard, revise page, split viewer, sign-in at 375px and the admin gate.
- Fixed a pre-existing contrast bug on the way: `SolutionButton`'s "AI
  solution" label used `text-accent-ink` on `bg-accent-soft` (white on pale
  blue in light, dark on navy in dark) — invisible in both.

Not done yet:
- **`ReviewQueue` itself** (969 lines, reviewer-only) got a light touch — its
  primary button, cards and empty state — and was not viewed signed in,
  because no reviewer account exists to sign in with.
- Dark mode: the white stacked logo is lazy-loaded (it is `display:none` in
  light), so a dark-mode visitor's first paint of the sign-in/landing logo
  can lag a moment. No layout shift — the space is reserved. Fixing it means
  either downloading both colourways for everyone or giving up the manual
  toggle for a `<picture>` media query.

---

## Known gaps

Concrete and verified this session, not carried forward from an old list:

- **Zero reviewer accounts provisioned.** `profiles.is_reviewer = true` has
  never been set on a real account in production. `/admin/review` is gated
  correctly, but nobody can currently pass the gate.
- **Item #7 is still open.** `question_options`: 144/~5,900 rows (one
  paper). `question_embeddings`: 0 rows, despite `VOYAGE_API_KEY` being set
  and the command being fully built and verified — it simply never got run.
  Most MCQs still display as a crop image with no selectable text, and the
  AI solver still can't use `match_question()`.
- **Automated tag verification exists and works, but needs a reliable model
  behind it.** `noteacademy tag-verify-auto` (new this session) reads each
  MCQ's own crop — genuinely independent of the first pass's text-based
  signal — and compares its topic choice to what's on file, same mechanism
  as the manual `tag-verify-export`/`tag-verify-apply` path. Live-tested
  against chemistry-5070: correctly caught a real, plausible disagreement
  (DB said topic `12.4`, the model's independent crop read said `7.3` —
  salt preparation from an insoluble carbonate) on the very first question
  it disagreed on. Stalled at ~74/272 chemistry-5070 MCQs, not because the
  tool is wrong but because ModelScope's vision access proved unreliable.
  `tag_from_crop` now targets DeepSeek's `deepseek-flash` (see above); it
  needs a real key and one supervised run before it can be trusted to run
  unattended at scale.
- **Tagging accuracy has only been independently checked for one-fifth of
  the bank.** Physics 5054's 600 MCQs were verified by a second, independent
  pass (564 agreed, 26 flagged, 10 human-decided). Chemistry 5070, biology
  5090, and all 1,858 structured questions — 2,398 of 3,098 tagged questions —
  rest on a single, never-checked first pass. Both verifiers now exist for
  the 2016-2026 range (545 distinct chemistry/biology MCQs; 1,159 structured
  questions) but neither has been run at scale yet.
- **841 approved structured questions carry a tag below the 0.75 confidence
  floor**, none still flagged for review (Biology 328, Chemistry 451, Physics
  62). Cause not investigated. These are the first place to point the
  structured verifier.
- **`/api/solve` can burn a quota unit for nothing.** No error handling
  around the model call itself; a transient failure charges the quota with
  no refund and shows a misleading error. Flagged, not yet fixed.
- **The split viewer's real documents aren't uploaded.** Interaction shell
  is done; the panes have nothing to render yet.
- **No examiner reports exist in the data**, so the "examiner comments" feature
  cannot ship yet: `questions.examiner_comment` is populated on 0 of 8,489
  questions and `paper_documents` holds only question papers and mark schemes.
  The site no longer *promises* them (landing page and the three meta
  descriptions were corrected 2026-09-21). The arena, topic page and split
  viewer already render an examiner note/tab only when one exists, so
  ingesting the reports would light the feature up with no further UI work.
- **One approved structured question cannot be marked:**
  `chemistry-5070-2014-may-june-p22` `A6` (no leaves, `max_marks` null).
- **Question crops have no reserved space**, so every question page shifts
  layout as its image loads. `question_assets.width_px/height_px` are unset
  (0/4,279); the aspect ratio can be derived from the stored `bbox` instead.
- ~~**The dashboard shipped ~100 kB of inline props.**~~ Fixed 2026-09-21. The
  subject dashboard's production HTML fell from 148 kB to 33 kB (largest inline
  script 100 kB -> 7.9 kB); the index page is 25 kB. Two causes, both removed:
  a 600-row question->topics table per subject, and every `Topic` with its
  full learning-objective text (now `TopicLabel`: code, slug, title). The
  dashboards now ask `POST /api/question-meta` about only the questions the
  student has attempted (no attempts, no request; results cached per page
  life). **Deliberately not `topic_mastery`**, which migration 0016 named as
  the follow-up: it is a materialised view (stale between refreshes, its own
  comment says "refresh nightly"), and it counts differently from the
  client's `computeTopicStats` (`is_correct is not null`, tag join), so
  adopting it would have changed students' numbers. If per-user aggregation
  ever needs to move into the database, make it a plain `auth.uid()`-filtered
  view over `current_answers`, not the matview.
- `attempts` is 0 rows in production: the signed-in path has never run
  against real user data.
- **Payments are schema-only** (`payment_submissions`) — no UI, no flow.

---

## First-pass tagging of the Paper 1 backfill (2026-09-21)

The route for the 2,960 newly loaded MCQs, run end to end for both subjects.
**Nothing is approved and nothing is live to students.**

- `mcq-options` (DeepSeek vision reads each question's stem and options) was run
  on all 84 in-scope variant 11/12 papers, 5 at a time, in about 25 minutes,
  none failed. 3,323 of 3,360 questions got text. The other 37 (figure-only
  questions, mostly) are not tagged: 22 still have no text after a retry.
- **`noteacademy tag-auto`** (new; `tag_auto.py`): reads each untagged MCQ's stem,
  options and keyed answer, asks `deepseek-v4-pro` for a topic from the closed
  list, writes it. Dry run by default; no DB connection during model calls;
  questions with no text are skipped, not guessed at. Result: **2,900 tagged**
  (Chemistry 1,315, Physics 1,583); 3 calls returned truncated JSON and
  succeeded on retry. Sampled tags read correctly; a stem like "Which
  statement is not correct?" leans entirely on the options.
- **`tag-verify-auto`** (second read, of the printed crop) on both subjects:
  Chemistry 1,391 agreed / 276 disagreed; Physics 1,580 agreed / 78 disagreed.
  Unapproved disagreements are now `needs_review` with `low_tag_confidence`
  (Chemistry 123, Physics 73). **12 calls failed** (max_tokens cut off, or
  malformed JSON) and are unverified; a re-run would re-read everything.
  Chemistry disagrees ~16% of the time against Physics ~5%; given ~15%
  run-to-run noise on identical runs, treat these as flags for a person.
- **Mistake, found and fixed, then cleaned up.** The MCQ model verifier, unlike
  the structured one, wrote to *approved* questions on a disagreement: primary
  confidence lowered to 0.74 and a model-suggested secondary topic (0.6) added.
  This run did that to **153 approved Chemistry and 5 approved Physics
  questions** (status unchanged, so still live). The verifier now only reports
  those (`leave_approved`; regression tests added; the manual `tag-verify-apply`
  path is unchanged). The 158 added secondary rows were deleted afterwards
  (exact-count guarded). **Still wrong:** those questions' primary confidence
  stays at 0.74; the original was overwritten and is not recoverable (the
  untouched approved ones sit at 0.9). Approval, not confidence, gates what
  students see. `question_topics.confidence` is a float4, so `= 0.74`
  comparisons miss; use a range.

- **Accuracy check against the approved tags (read-only), 2026-09-21.** The
  first-pass tagger was run, in memory, on every approved question with text
  and compared with the topic already approved. **Physics agrees on 68 of 77
  (88%)** and the 9 differences are mostly neighbouring topics. **Chemistry
  agrees on only 157 of 316 (50%), and the disagreement is concentrated in four
  papers whose approved tags are wrong, not the tagger:**
  2019 May/June P11 (0/40), 2019 May/June P12 (11/40), 2020 Oct/Nov P11 (6/38),
  2020 Oct/Nov P12 (3/40). The other four approved Chemistry papers match at
  78-92%. In the worst paper the approved tags run in ascending syllabus order
  regardless of content ("rate of reaction" filed under Preparation of salts).
  These 159 questions were approved, so live to students, under the wrong
  topics. **Fixed 2026-09-21:** `tag-auto --retag --paper <slug>` (refuses to
  run without a named paper; saves the old tags to
  `pipeline/work/retag-before-<slug>.csv` first) re-tagged all four papers from
  text: 139 of 159 topics changed. Every question in them, plus one with no
  readable text (2020 O/N P11 Q3), is back in `needs_review` with
  `low_tag_confidence`, so none is live until a person re-approves it.
  Chemistry approved MCQs went 318 -> 160. This also means the 153 Chemistry
  disagreements on approved questions found by the second read were largely
  real.

## Chemistry MCQ review queue cleared by hand-reasoning (2026-09-21)

All **283** chemistry MCQs in the review queue were read individually against the
49-topic syllabus (by Claude, not by the pipeline classifier) and decided.
**275 approved, 8 held.** Chemistry approved MCQs: 160 -> 434.

- **85 tags changed.** The classifier's errors were systematic, not random:
  uses of sulfuric acid / SO2 filed under *Air quality* instead of with the
  Contact process (7); gas-volume and combustion stoichiometry under *Formulae*
  instead of *The mole* (8); "identify the element from its properties" under
  *Properties of metals* when the point is *transition elements*; ester and
  carboxylic-acid questions under *acids and bases* (5).
- **Rules applied consistently across duplicate questions** (the variants share
  most items): formula asked *from* charges -> 3.1, charges asked *from* a
  formula -> 2.4; plain percentage-by-mass -> 3.2 (no mole needed); naming
  general apparatus -> 12.1, choosing a setup that follows a rate -> 6.2.
- **8 held:** one with no answer key (2017 M/J P11 Q25), one with no extractable
  text (2020 O/N P11 Q3 - needs a look at the crop), six that genuinely span two
  topics.
- Tags written with `source = 'model'` and `reviewed_by` left null: this was a
  careful reading, but still a model's, and the audit trail should not claim a
  person checked them.
- **Bug found while doing it:** replacing a primary tag leaves the old one behind
  as a *secondary* (`apply_worksheet` unsets `is_primary` rather than deleting),
  so a retagged question still lists under its old topic. 85 such rows were
  cleaned up here, but **`tag-auto --retag` and `tag-apply` still do this** -
  worth fixing in code.

### Paper 2 (structured) spot check - a real problem

Five random *approved* chemistry Paper 2 questions were read in full, sub-parts
included. Three were correctly tagged (the stem misleads - e.g. a question
opening "the reaction between ethene and bromine" is correctly filed under
*Exothermic and endothermic* because 6 of its 9 marks are bond-energy and
reaction-pathway work). One was borderline. **One was clearly wrong:** 2026 M/J
P22 Q6, filed under *Extraction of metals*, where only 2 of 12 marks are
extraction and the bulk is alloys and corrosion.

The counts underneath that are worse than the sample:

| subject | approved structured Qs | tagged below the 0.75 floor | below 0.6 |
|---|---|---|---|
| chemistry-5070 | 572 | **451 (79%)** | 294 |
| biology-5090 | 527 | 328 (62%) | 242 |
| physics-5054 | 667 | 62 (9%) | 23 |

None has a `source='human'` tag. So the chemistry and biology structured banks
were approved wholesale with tags the classifier itself flagged as unsure, and
they are live to students now. Physics is in far better shape.

## Structured tag verification, and three bugs it uncovered (2026-09-22)

Running the second read over the structured banks, and fixing what doing it
exposed.

**All three structured banks re-read from their printed page-crops.** Every
tagged top-level question, 2010-2026:

| subject | read | agreed | reordered | disagreed | below floor, before -> after |
|---|---|---|---|---|---|
| chemistry-5070 | 613 | 420 | 144 | 49 | 451 -> 174 |
| biology-5090 | 568 | 458 | 93 | 17 | 328 -> 97 |
| physics-5054 | 677 | 446 | 159 | 72 | 62 -> **95** |

Across the three: **841 -> 366 below the floor**, and 1,257 of 1,766 approved
structured questions now carry a tag two independent reads agree on, where
before none did.

**Physics went up, and that is the verifier working.** Its 72 disagreements
were all on approved questions, so each had its confidence lowered to 0.74 and
nothing else touched — 65 questions that had been sitting above the floor
looking settled are now marked as disputed. The tags did not get worse; what
is recorded about them got honest. Biology, by contrast, finished with none at
0.74: its two reads simply agreed far more often (81% confirmed, against 66%
for physics and 68% for chemistry).

**Why the remaining 174 were left alone.** Four of the disagreements were
audited by reading every sub-part and asking which topic carries the most
marks. The second read was better twice, *worse* once (2018 M/J P22 Q2: filed
under Redox, which owns 4 of its 7 marks; the model wanted Transition elements,
which owns 2), and a toss-up once. Adopting the second read wholesale across
173 questions would therefore have been close to a coin flip and would have
regressed a question that was already right. The triage CSV has each one with
both candidates and the model's reasoning; they need a reasoning pass or a
person, not a rule.

**The signal that looked worth encoding — and the test that killed it.**
Three of those four calls came down to *which topic the marks sit under*
rather than what the opening sentence is about, so the obvious next move was
to tag each leaf part separately and let the marks pick the winner. Tried on
the same four questions, where the right answer was already known, it got one
right and three wrong or meaningless:

  * **Ties are the normal case, not the exception.** Three of the four ended
    in a tie (a 6-way one for a "choose from these elements" question, whose
    six 1-mark parts are six different topics). Many structured questions
    genuinely have no dominant topic, and a tie-break invents an answer.
  * **Per-part tagging brings its own errors, and they are amplified.** In
    2018 M/J P22 Q2 a 2-mark redox observation was tagged *Electrolysis*,
    which split redox's marks and handed the question to the wrong topic —
    the exact regression the whole exercise was meant to prevent.
  * **The marks are not complete enough to weight by.** In 2014 O/N P21 A5
    only one of six parts had a mark stored, so the weighting was decided by
    a single part.

So: no rule. The remaining below-floor tags need a reasoning pass or a person.
Recorded here so the idea is not re-attempted from scratch.

Separately, 510 of 8,641 leaf parts (6%) store a `max_marks` that disagrees
with the `[n]` printed in their own text — some off by a lot (33 against a
printed 5). It is tolerable for `total_marks`, which aggregates hundreds of
parts per topic, but it is not a foundation to build per-question logic on.

### Three bugs

1. **The replaced topic was demoted, not deleted.** `apply_worksheet` and the
   review route's `assignTopic` both unset `is_primary` and left the old row,
   so every correction silently added a *second* topic the question went on
   being listed under. Live data had 85 such rows from the chemistry MCQ retag,
   plus one physics question carrying a reviewer's own superseded choice beside
   their later one. Both paths now delete the row they replace; 10 leftovers
   cleaned out; 7 tests.
2. **`bulk-approve-structured` published unverified tags silently.** It vets a
   question's content and deliberately not its topic tag — defensible, except
   approving publishes both, which is how 451 chemistry questions went live
   under topics the classifier itself doubted. It now counts and warns. The
   gate is unchanged: that is a product decision.
3. **A failed write pass threw away every model call.** Two runs of ~900 vision
   calls were lost — the first to `server closed the connection unexpectedly`,
   the second to a statement timeout. The answers are now written to JSONL at
   the phase boundary, and `tag-verify-structured --from-results <file>`
   replays them: one transaction per question, a short `lock_timeout`, locked
   rows skipped and reported. The replay then wrote all 613 with nothing lost.

**The incident behind the second failure**, worth knowing about: the first
crashed run left a session *idle in transaction* holding row locks on
`question_topics`, and this database has
`idle_in_transaction_session_timeout = 0`, so it would never have expired on
its own. It eventually went when the pooler reclaimed it. Setting that timeout
to something finite would turn a hard block into a self-clearing one.

### The topic count said 76 where the page showed 23

`v_topics.question_count` counted duplicate MCQs the topic page folds (8),
structured questions it never lists (45), and left approval filtering to RLS,
so the same view answered differently depending on who asked. `0032` counts
approved, non-duplicate MCQs instead. `total_marks` is untouched: "what is this
topic worth" and "how much can I practise here" are different questions.
**Expect the badges to drop sharply** — physics Momentum reads 1, not 12 —
because most physics MCQs are not approved yet. The numbers are honest now and
climb as the queue is reviewed.

Alignment on the same list: topic codes are different lengths ('1.8' against
'1.7.1'), so each title started wherever its code ended. The code now sits in a
fixed-width column, and a section heading carries the same padding as the boxes
beneath it rather than hanging into the gutter.

---

## The 366 remaining structured tags, read and decided (2026-09-22)

Every below-floor approved structured question in all three subjects was read
in full -- stem and every sub-part, with each part's marks -- and given a topic
by hand. **366 decided, 254 tags changed, 345 now above the floor, 21 left
deliberately flagged.**

| subject | decided | tag changed | still flagged |
|---|---|---|---|
| chemistry-5070 | 174 | 106 | 6 |
| biology-5090 | 97 | 60 | 9 |
| physics-5054 | 95 | 88 | 6 |

Across the three banks the below-floor population went **841 -> 21**.

**The rule that did the work:** a structured question's topic is wherever its
*marks* sit, not what its opening sentence is about. That single test decided
most of these, and it is what both classifiers get wrong -- they follow the
framing. Examples: a question opening "the reaction between ethene and bromine"
is energetics, because 6 of its 9 marks are bond energies and a reaction
pathway; one opening "Alkanes are a homologous series" is kinetic particle
theory, because 6 of its 10 marks are about melting and boiling; a physics
question titled around a skeleton carries its marks in blood and gas exchange.

**Three refinements the reading forced:**

  * *"Choose from the following..."* recall questions spread 5-7 topics over
    single marks and have no dominant one. They take the umbrella the choosing
    is done from -- elements to 8.1, formulae to 3.1, compounds identified by
    their tests to 12.5 -- at 0.75, not higher.
  * One calculation inside an otherwise thematic question does not make the
    question "about the mole". The marks rule decides between competing
    *themes*, not between a theme and an arithmetic step.
  * A question genuinely split four ways keeps a confidence below the floor.
    That is what the 21 flagged ones are: not unfinished, decided to be
    undecidable. One of them (biology 2016 O/N P21 Q3) is mostly about the
    skeleton, which this syllabus does not cover at all.

Tags are written `source = 'model'`. A careful reading is still not a person's,
and 21 of them still say so.

---

## The figure-only MCQs and the failed verification calls (2026-09-22)

**22 figure-only MCQs, tagged from their printed crops.** These are the
questions `mcq-options` could find no text in and `tag-auto` therefore skipped:
stem and options all artwork -- a circuit, four vector diagrams, a heating
curve. There is only ever a handful per subject, but they were untaggable and
so invisible to students for good. New `tag-auto --from-crops` reads the crop
with the same vision call `tag-verify-auto` uses, and tags what it sees:
5 chemistry, 17 physics, none failed. **Their confidence is capped below the
floor on purpose** -- one read of a picture, with no text and no second opinion
to check it against, is a lead for a person, not a verdict -- so all 22 sit in
the review queue. Six tests, including one asserting no connection is open
while the crop is being read.

**The 12 failed verification calls, retried and finished.** Seven had died on
"reply was cut off by max_tokens before any answer was produced", which is
thinking mode eating the token budget; the rest returned malformed JSON. Re-run
with thinking off, **all 12 succeeded**: 9 agreed with the tag on file, 3
differed. They were retried individually rather than by re-running their
papers, since every other question on those papers already had a verdict and
re-reading them would only add churn from run-to-run noise.

Two of the three disagreements were settled by reading the crop: 2024 O/N P12
Q30 asks in its own words for the "possible method of extraction", so 9.6 and
not the reactivity series that gets you there; 2018 O/N P12 Q12 cannot be
answered without its efficiency step, so 1.7.4 and not Power. The third
(2022 M/J P11 Q8) compares a metallic, an ionic, a giant covalent and a simple
molecular structure at once, and is left flagged because no one of them is the
question.

---

## The physics MCQ queue cleared, and 29 published questions corrected (2026-09-22)

**The physics MCQ review queue is empty.** All 90 were read individually --
73 flagged by the verifier plus the 17 figure-only ones tagged from crops --
and decided against the published learning objectives. **90 decided, 48 tags
changed, all 90 approved.** Physics approved MCQs 435 -> 525.

**Two rules did most of the work, and both came from reading the actual LOs
rather than reasoning from the topic titles:**

  * 1.5.1's resultant objective is limited to forces *"along the same straight
    line"*, so every non-collinear vector question belongs to 1.1, not Forces.
    That moved the scale-diagram resultant, the pendulum force triangle and
    the parallelogram question.
  * The topic is whatever **discriminates between the four options**, not what
    the stem's scenery is about. A transformer question whose answer is
    24/26.4 is Efficiency, not The transformer; a sound trace read off a
    time-base is Uses of an oscilloscope, but the same trace asked about the
    *generator coil's rotation* is The a.c. generator.

Duplicates were made consistent as a side effect: the refrigerator question
appears three times and was tagged two different ways; "a motor rated 10 W for
5 minutes" appears three times; the infrared-in-a-vacuum question twice.

**The 17 crop-tagged questions validated the `--from-crops` path**: read
visually, 16 of 17 were already right. Only the 2018 O/N P11 Q37 nuclear
reaction moved -- it is D-T fusion, but its options turn on nucleus-vs-atom and
proton-vs-neutron, so it is 5.1.2 and not the 5.2.3 it had.

**29 already-published questions were carrying an unresolved verifier
disagreement.** They had a second-opinion row attached and, in most cases, a
primary confidence knocked to 0.74 -- below the review floor -- yet stayed
approved: residue from the verifier run that predated `leave_approved`. Each
was read from its crop. **17 tags changed, 12 confirmed.** The model had been
right in 13 of the 17; in two (the parachutist terminal-velocity pair) *both*
the file and the model were wrong, and 1.5.2 owns it because "explain how an
object reaches terminal velocity" is its objective.

**Why those 29 mattered rather than being cosmetic:** neither
`v_topics.question_count` nor `v_mcq_questions.topic_codes` filters on
`is_primary`, so an attached second opinion *lists and counts* the question
under a topic nobody chose. All 29 are cleared; physics now has no non-primary
MCQ rows at all.

**A live hazard this exposed, not yet fixed.** `verify.py` records a second
opinion as a non-primary row at exactly 0.6. The web review route clears only
`is_primary` rows when a reviewer overrides a topic, and touches
`question_topics` not at all when a reviewer simply approves -- so approving a
flagged question leaves the *rejected* suggestion attached, and the two views
above then publish it. Seven such rows exist today, all on questions still
awaiting review, so nothing is wrong on the site right now; the exposure is the
234 questions still queued. **Do not "delete all non-primary rows on approve"**
-- 167 structured rows at 0.80-0.90 are genuine editorial secondary topics from
the original tagger. The fix is to make the verifier's second opinion
distinguishable by intent (a `verifier` value on the `tag_source` enum) instead
of by a magic 0.6, then have the approve path drop those and only those.

**Physics structured: 2 decided, 29 blocked on a different defect.** The two
`low_tag_confidence` ones were settled by the marks rule (a 15-mark loudspeaker
question is Sound, not Forces on a current-carrying conductor -- the motor
effect carries 3 marks of 15; a radon-222 question is The atom, because
alpha-particle scattering carries 4 of 9). The other 29 are flagged
`unmatched_mark_scheme` and are not a tagging problem at all -- see below.

**Topic coverage: 60 of 63 revisable physics topics have an approved MCQ.**
The three empty ones (2.1.1, 2.3.4, 6.1.1) all have questions waiting in the
1,516 not-yet-approved pool -- 2.1.1 alone has 18 -- so they fill on
`bulk-approve`. 2.1.1 emptied because every particle-arrangement question
genuinely belongs to 2.1.2, whose objective is the one about the forces,
distances and motion of particles; 2.1.1 is macroscopic properties.

---

## Mark-scheme extraction: 262 -> 136 missing, and what is genuinely left (2026-09-22)

Found while clearing the physics queue: **262 structured leaf questions across
all three subjects had no mark-scheme text**, so a student practising them got
no answer. The source PDFs are all on disk (nothing needed downloading); this
was entirely a parser problem. Investigating it corrected an earlier read of
the same bug (see below) and turned up three genuinely different causes,
only one of which was actually fixed today.

**91 of the 262 were already fixed, just never re-ingested.** Diagnosing the
original 4-entries-from-a-14-page-mark-scheme failure with `known_questions`
supplied the way the real ingest path always supplies it (unlike the ad hoc
first repro, which called the parser with `known_questions=None` and made it
look far more broken than production actually was) showed most of the corpus
already parsing correctly against the *current* code. Some improvement made
earlier in this session — or possibly further back — had never been carried
into the database by a re-run. `ingest_structured_paper`'s upsert is
idempotent and protects anything already approved (status, review_flags, and
it never touches `question_topics`), so simply re-ingesting every affected
paper was safe, and recovered 91 questions for free.

**A real, narrow bug was found and fixed: a stray scientific-notation
exponent.** `3.0 × 10⁸` is a true superscript glyph in these PDFs, and
PyMuPDF's text extraction sometimes assigns it to a "line" of its own rather
than merging it into the row it visually sits beside — and because it is
raised, that stray line can extract *before* its base rather than after it:
`8` arrives as its own line immediately ahead of `3.0 10`. A bare `8` there
satisfies every check `parse_structured_mark_scheme` already has for "this is
a genuine new question root" (ascending, a known label, alone on its own
line) — exactly the same shape as the nuclide-mass-number case the parser
already special-cases (`_NUCLIDE_CONTINUATION_RE`) — and is told apart from a
real root the same way: by what the next line looks like
(`_SCIENTIFIC_NOTATION_BASE_RE`, matching scientific notation's own
un-multiplied base, "3.0 10"). Recovered 5054 w25 ms 22 from 4 usable entries
out of a 14-page mark scheme to 42 of 43, the last one a genuinely
content-free answer (see below). Six new tests, purely textual — this makes
*no* change to how any PDF is read, only to which lines the existing
line-by-line grammar is willing to read as a question boundary.

**A geometric fix was attempted and rejected.** Reconstructing "logical
rows" from PyMuPDF's raw span geometry (grouping fragments by vertical
overlap rather than trusting its own line grouping) recovered far more —
91 questions in testing — by also gluing subscripts, split same-row tokens,
and other superscript shapes back together. It was not shipped: it corrupted
reading order on at least one table-column layout (scrambling
`Question`/`Mark` column values across rows on a biology paper, badly enough
to crash `_root_ordinal` on a garbled label) and needed a second, tighter
pass to stop nuclide subscripts occasionally gluing onto the wrong neighbour
(a bare root a few points away, rather than their own element symbol) even
after that first crash was fixed — two real defects found by testing against
every structured paper in the corpus before either shipped, not two ways of
looking at the same one. A fix whose failure mode is silently reordering a
mark scheme is worse than the flag it would clear, so it was reverted in
favour of the smaller, purely-additive regex above. If this is revisited, it
needs to respect PyMuPDF's own block boundaries rather than discarding them.

**136 remain, for two reasons, neither of them the 2025-format bug:**

  * **Genuinely content-free answers** (a handful, e.g. 5054 w25 ms 22's
    `5(a)`) — the mark scheme prints a bare mark code with no answer text at
    all, presumably a diagram-labelling point. Correctly flagged
    `unmatched_mark_scheme` for a person; there is no text to recover.
    Likewise a few genuine "merged parts" (one mark-scheme entry covering
    three question-paper sub-parts at once) and a couple of EITHER/OR
    branches whose own answer is a bare mark code — all real ambiguities
    the matcher is deliberately conservative about, not bugs.
  * **A different, older mark-scheme grammar the parser was never built to
    read** (the large majority of what remains — 80 of 136 in biology alone,
    including 29 of 29 leaves on one 2013 paper). This style has no B/C/M/A
    mark codes at all: marking points are semicolon-separated, and the
    "Mark" column is a bare number sitting alone on its own row — the exact
    same shape as a bare question number opening a new question. The two are
    normally told apart because a document either has mark codes (so a bare
    number is always a root) or doesn't (so it's always a mark value) — but
    this table style can print *both* on their own row in the same document,
    an ambiguity the current line-by-line grammar cannot resolve from text
    alone. Resolving it needs the PDF's column geometry (a "Mark" column
    value sits at a different x-position than a "Question" column value),
    which means passing position data into the parser rather than plain
    strings — a real feature, not a quick fix, and one this session did not
    attempt given how narrowly the geometric approach above already went
    wrong once.

---

## Bulk-approve, the second-opinion leak, and the solver refund (2026-09-23)

**2,748 MCQs bulk-approved** after a person read a 60-question stratified
sample (one question per topic, spread across sittings) and found nothing
wrong. Physics 525 -> 2,041 approved MCQ rows, chemistry 386 -> 1,618 (1,802
and 1,462 distinct once dedupe was re-run — see below), and every
revisable topic in both subjects now has questions — the three physics topics
that were empty (2.1.1, 2.3.4, 6.1.1) filled from this pool, as expected.
`bulk_approve` only takes a question with empty `review_flags` and a primary
tag at or above the floor, so none of them could have carried a verifier
second opinion (that write always sets `low_tag_confidence`).

**The verifier's second opinion is now its own `tag_source`.** 0033 adds
`'verifier'`; 0034 relabels the 7 existing rows (non-primary, `model`,
confidence 0.6 — a value nothing else writes) and touches none of the 167
genuine editorial secondaries. `verify.py` and `verify_structured.py` now
write `'verifier'` directly, including on the `on conflict` update. The
review route deletes `source = 'verifier'` non-primary rows for the whole
question group on every approval — with or without a topic override — so a
rejected suggestion can no longer be published under a topic nobody chose.
The MCQ path (`verify._apply_one`) still has no direct unit test; the
structured one now asserts the literal.

**`/api/solve` refunds a quota unit it charged for nothing.** `consume_quota`
runs before the Gemini call, so a thrown request or an empty reply used to
cost a student one of five daily solves for no answer. 0035 adds
`refund_quota` (floored at 0, so it can only return units the day's row
holds); the route calls it on both failure paths. A cache-write failure after
a real answer is still charged — the student got the solution.

**A regression from the mark-scheme re-ingest, found and fixed.**
`upsert_question` replaced an undecided question's whole `review_flags` array
with whatever ingest derived, and ingest only ever derives
`unmatched_mark_scheme`. So re-ingesting 58 papers to recover mark schemes
also wiped `low_tag_confidence` from **18 top-level structured questions**
(11 chemistry, 7 biology, first-pass tags at 0.40–0.70, never reviewed),
dropping them out of the review queue as unflagged `extracted` rows a later
`bulk-approve-structured` would have published. All 18 were put back
(`needs_review`, flag restored), and the upsert now replaces only the flag
ingest owns, keeps every other, and keeps a `rejected` decision the same way
it already kept `approved`. Proved against the live database inside
rolled-back transactions on two papers: a restored `low_tag_confidence`
survives a re-ingest, and a still-unmatched leaf keeps exactly one
`unmatched_mark_scheme`. No MCQ papers were re-ingested, so the 8 rejected
MCQs were never at risk. The chemistry structured queue (133) settles at 52
for the right reason: leaves whose mark schemes were recovered left it; the
11 restored ones are back in it.

**Option text for the last 21 Paper 1s.** 13 physics papers (2010-2012, 2015
M/J P11) and 8 biology papers (2019-2020) had approved MCQs with no stem or
option text — playable from the crop, but invisible to search and giving the
solver nothing to work from. `mcq-options` on DeepSeek `deepseek-flash`, the
same path as the 2016-2026 batch, p11 before p12: every run exited cleanly.
Approved MCQs without text: physics 380 -> 30, biology 265 -> 5; what is left
is questions whose options are diagrams. Most runs "failed cross-check" on a
page or two — the same rate as the earlier batch (74 of 84 logs), and it only
reports, it does not block the write. So the written data was checked
independently against each PDF's own text layer: **of 813 extracted stems,
795 sit immediately after their own question number**; every one of the 18
exceptions opens "The diagram shows ...", where the text layer interleaves
diagram labels or the check finds an earlier question with the same opening,
and read by eye each one matches its question.

**Dedupe had never been re-run after the 2016-2026 Paper 1 backfill.** 159
physics and 48 chemistry MCQs were marked as cross-variant repeats; a fresh
run finds 398 and 205. So roughly 400 reused questions were appearing twice in
topic drills and double-counted in every topic badge. Two fixes to
`apply_dedupe` / `group_duplicates` first, then a real run on all three
subjects:

  * **The answer key is part of a duplicate's identity.** Chemistry 2019 O/N
    Q33 (key A) and 2024 M/J Q33 (key D) print word for word the same, because
    the four alkane structures they ask about are drawn, not written. Grouped,
    the newer one would have hidden the older from every drill. Now
    `(text, correct_option)`; two tests.
  * **Every pointer in the subject is cleared before re-deriving**, not just
    those in this run's groups — the docstring already promised this.
    Chemistry 2020 M/J P12 Q40 held a pointer from an older run to its P11
    twin, which CAIE had edited by one word ("contain **the** amide
    linkages"); nothing would ever have re-derived it.

Checked before running: no group has a canonical (newest sitting) that is
unapproved while a member is approved, so nothing approved is hidden behind
something students cannot see. Every physics and chemistry topic still has
questions.

---

## The review queue read by hand, and a re-ingest regression fixed (2026-09-23)

**Every flagged question in the 2016–2026 range was read and decided.** Not
run through a heuristic — each one opened from its own crop or its real mark
scheme PDF, checked against the syllabus's own learning objectives, and
written up with the reasoning in the decision file before being applied.

  * **14 chemistry MCQs**: 13 approved (several retagged — e.g. a
    "which statement is correct" question about mixed states was 2.1, not
    the reactivity-series topic it had; a dot-and-cross ethene question was
    already right), 1 rejected. That one (2017 M/J P11 Q25, lead(II)
    sulfate) turned out to have no answer at all — Cambridge's own mark
    scheme says "Question discounted" — so there was nothing to approve.
  * **11 chemistry and 7 biology structured questions**, `low_tag_confidence`
    from the first tagging pass: each retagged by the marks-weighted rule
    already established this session (the topic is where the marks sit, not
    what the opening sentence is about), then approved.
  * **All 57 structured parts in the 2016–2026 range that were flagged
    `unmatched_mark_scheme`**: read from the real PDF (most were legible
    once pulled from the right page; a few needed the page rendered as an
    image, since the printed answer was a diagram — a drawn NAND-gate
    symbol, an electromagnetic-spectrum diagram with regions marked, a
    nuclide equation). Every answer was written into `mark_scheme_text` by
    hand, backed by a `.before.csv` snapshot of every row touched before any
    write.

**This did not fix the parser.** The underlying limitation from the
2026-09-22 write-up — a mark-scheme table style with no B/C/M/A codes, where
a bare "Mark" column value is indistinguishable from a bare question number
by text alone — is still there. What happened here was a one-time manual
pass that supplied the missing answer text directly, for every question
currently in the queue. A newly ingested paper using that same older layout
will trip the same `unmatched_mark_scheme` flag again; nothing here prevents
that.

**A second `upsert_question` regression, found proving the first fix.**
Re-ingesting a paper to attach a hand-read mark scheme re-derived
`unmatched_mark_scheme` on the very same part, because the old guard checked
only the *incoming* flags, not whether the row already had text. Fixed:
the flag is now dropped on conflict whenever `questions.mark_scheme_text` is
already non-empty, so a hand-attached answer (or one a future ingest run
finally manages to parse) can never be silently re-flagged by a later
re-ingest. Proved against the live database in a rolled-back transaction
(a hand-attached physics answer survives a fresh re-ingest unflagged).

**Scope narrowed by the user mid-session**: only 2016–2026 papers are in
scope going forward. One item was mid-flight when that landed —
`biology-5090-2013-may-june-p22` (29 structured parts, an older mark-scheme
layout) — and was left exactly as found, deliberately unflagged-clear. It is
the only paper still in the review queue. Chemistry structured papers
2010–2015 and one biology 2012/2015 pair were already read and fixed earlier
in this same pass, before the scope was narrowed; that work stands (correct,
harmless, already committed to the database) and was not reverted.

**Final state**: physics and chemistry review queues empty, 63/63 and 49/49
revisable topics covered. Biology: 47/52 (the remaining 5 topics' only
approved-eligible questions are on the 2013 paper). The one queued item is
the 2013 paper, out of scope. 344 tests passing, lint clean.

---

## Next steps, in priority order

1. ~~**Fix the `/auth/callback` open redirect.**~~ Done.
2. ~~**Commit and push the outstanding pipeline work.**~~ Done.
3. ~~**Finish the topic tags.**~~ Done: 2,748 MCQs bulk-approved after a
   clean sample, and every flagged question in the 2016-2026 range read and
   decided by hand. Physics and chemistry review queues are empty; both
   subjects have 100% revisable-topic coverage.
4. ~~**Stop the review route publishing rejected second opinions.**~~ Done
   (0033/0034, `source = 'verifier'`).
5. **Scope is now 2016-2026 only** (set by the user 2026-09-23). One item is
   deliberately left as found: `biology-5090-2013-may-june-p22` (29
   structured parts, still `needs_review`), the only paper left in the queue.
6. **The older, no-mark-code mark-scheme grammar is still unsupported by the
   parser.** Every part currently affected in the 2016-2026 range was hand-
   fixed rather than the parser being taught to read it (see the write-up
   above), so this is not urgent — but a paper newly ingested in that layout
   will trip the same `unmatched_mark_scheme` flag. Needs the parser to read
   column x-position from the PDF, not just line-by-line text, if it is ever
   worth fixing properly.
7. **Add a payment method to the Voyage account, then run `embed` for
   physics-5054.** Fully built and tested; blocked only by the free-tier rate
   limit. Turns on the AI solver's real `match_question()` path and unblocks
   similar-question nudges.
8. ~~**Finish `mcq-options`.**~~ Done for every paper (see 2026-09-23); 35
   approved MCQs stay crop-only because their options are diagrams.
9. ~~**Provision at least one reviewer account.**~~ Done.
10. ~~**Patch the `/api/solve` quota-refund gap.**~~ Done (0035 `refund_quota`).
11. **Run tagging verification at scale for chemistry-5070, biology-5090,
    and physics's own structured bank**, once #3 lands. The tool works
    (proven on real data); it just needs a provider it can run unattended
    against.
12. **Upload the source PDFs and finish the split viewer.** Unlocks the
    `SplitViewer`'s real panes instead of placeholders.
13. **Payments**, once there's a live audience to charge.
14. ~~**Finish the redesign.**~~ Done, apart from viewing the reviewer queue
    signed in.
15. **Speed**: reserve crop space from `bbox` (the dashboard payload is done).
16. **Ingest examiner reports** (the site no longer promises them, but the UI
    is ready): needs the source documents acquired first.
