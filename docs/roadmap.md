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
| Physics 5054 | yes | 594 | 667 | 81 |
| Chemistry 5070 | yes | 318 | 572 | 74 |
| Biology 5090 | yes | 312 | 527 | 73 |
| Mathematics 4024 | no | 0 | 0 | 0 |

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

---

## Next steps, in priority order

1. ~~**Fix the `/auth/callback` open redirect.**~~ Done.
2. ~~**Commit and push the outstanding pipeline work.**~~ Done.
3. **Finish the topic tags.** Chemistry MCQs are done; chemistry structured is
   down to 174 below-floor tags and biology is being verified. Remaining, in
   order of how live the damage is: (a) the ~174 chemistry and biology
   structured disagreements — a reasoning pass against the marks, not a rule
   (see above); (b) Physics's 73 flagged MCQs; (c) the 22 figure-only MCQs that
   have no text to tag from (`tag_from_crop` or by hand); (d) the 12 MCQ
   verification calls that failed; (e) `bulk-approve` what is left, after a
   person has read a sample.
4. **Add a payment method to the Voyage account, then run `embed` for
   physics-5054.** Fully built and tested; blocked only by the free-tier rate
   limit. Turns on the AI solver's real `match_question()` path and unblocks
   similar-question nudges.
5. **Finish `mcq-options` for the remaining 14 physics-5054 papers**, once
   a reliable vision provider is wired up.
6. **Provision at least one reviewer account** (`update profiles set
   is_reviewer = true where email = '...'`). Costs nothing and unblocks the
   review queue, which currently has no one who can sign into it.
7. **Patch the `/api/solve` quota-refund gap.** Small, self-contained, and
   the failure mode (silently charging a student for a solve that never
   happened) is the kind of thing that erodes trust quietly.
8. **Run tagging verification at scale for chemistry-5070, biology-5090,
   and physics's own structured bank**, once #3 lands. The tool works
   (proven on real data); it just needs a provider it can run unattended
   against.
9. **Upload the source PDFs and finish the split viewer.** Unlocks the
   `SplitViewer`'s real panes instead of placeholders.
10. **Payments**, once there's a live audience to charge.
11. ~~**Finish the redesign.**~~ Done, apart from viewing the reviewer queue
    signed in.
12. **Speed**: reserve crop space from `bbox` (the dashboard payload is done).
13. **Ingest examiner reports** (the site no longer promises them, but the UI
    is ready): needs the source documents acquired first.
