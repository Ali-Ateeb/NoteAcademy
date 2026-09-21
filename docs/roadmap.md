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
  now an error rather than a silent fall-back to Gemini). **Written and
  unit-tested against DeepSeek's documented contract (236 pipeline tests,
  mocked transport); never run against the live API — no `DEEPSEEK_API_KEY` is
  configured yet.** The likeliest first-run surprises are the vision path
  (base64 `image_url` parts, with JSON mode) and the model names, both taken
  from DeepSeek's docs of 2026-09-21. Design points worth knowing:
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
  the bank.** Physics 5054's 594 MCQs were verified by a second, independent
  pass (564/600, 94%, agreed). Chemistry 5070, biology 5090, and physics's
  own 667 structured questions — 2,396 of 2,990 tagged questions — rest on a
  single, never-checked first pass.
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

## Next steps, in priority order

1. ~~**Fix the `/auth/callback` open redirect.**~~ Done.
2. ~~**Commit and push the outstanding pipeline work.**~~ Done.
3. **Add a `DEEPSEEK_API_KEY` (with a balance) and do one small supervised
   run** — `noteacademy tag-verify-auto --subject chemistry-5070` in its
   default dry-run mode — to prove the vision path against the live API before
   trusting it at scale, then run tagging verification for the subjects still
   resting on a single unchecked pass, and finish `mcq-options`.
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
