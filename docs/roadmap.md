# Where the app stands

Rewritten 2026-09-15 — the previous version of this file described a state
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
  timing, marked instantly, examiner's note shown after answering.
- Self-marked structured practice (`StructuredArena.tsx`) — untimed; the
  student marks their own handwritten answer against the real mark scheme.
  This did not exist when this file was last accurate.
- Topical drills — every question on one syllabus outcome, across every
  year, untimed. Verbatim MCQ duplicates across paper variants are folded to
  one card.
- Dashboard — accuracy per topic, worst first, from `localStorage` (signed
  out) or `current_answers` (signed in, cross-device via RLS).
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
- Both `extract.py` (vision) and `tagging.py` (direct-API tagging, plus a
  new `tag_from_crop` built specifically for automated verification — see
  below) can run against Qwen via ModelScope's API-Inference endpoint
  instead of Gemini, switched independently per stage by
  `NOTEACADEMY_EXTRACTION_PROVIDER` / `NOTEACADEMY_TAGGING_PROVIDER`. Tried
  for real this session with a live `MODELSCOPE_API_KEY` and found
  **unreliable enough not to depend on**: `Qwen-Ambassador/Qwen3.8-Max`
  gave persistent `504`s on every vision call across multiple attempts;
  `Qwen-Ambassador/Qwen3.7-Max` answers text instantly but returns a clean
  `500 "No choices in OpenAI response"` on every vision call specifically.
  Text-only calls work fine on both. Current decision: moving off ModelScope
  back toward Gemini (now with real billing, unlocking Gemini 3 Pro, which
  was previously hard-blocked at 0 free-tier quota) or the real Anthropic
  API for the vision-dependent work — not yet implemented for either.
- `mcq-options` (vision backfill of MCQ question/option text): 144
  `question_options` rows written (one paper, physics-5054's newest sitting,
  mostly done) before the ModelScope reliability problems above stopped
  progress. 14 of 15 physics-5054 MCQ papers still untouched.
- `embed` (retrieval embeddings): built, tested live against real data,
  **never actually run** — `question_embeddings` is still 0 rows. This
  fell off after `VOYAGE_API_KEY` was added; nothing is blocking it.
- 30 migrations, all applied to the live database with matching checksums
  (`db/apply.py --status`), including `0030` fixing the attempts/profiles
  cascade-delete bug.

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
  tool is wrong but because ModelScope's vision access proved unreliable
  (see above) — needs a Gemini or Anthropic vision path added to
  `tag_from_crop` before it can be trusted to run unattended at scale.
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
- **Payments are schema-only** (`payment_submissions`) — no UI, no flow.

---

## Next steps, in priority order

1. ~~**Fix the `/auth/callback` open redirect.**~~ Done.
2. **Commit and push the outstanding pipeline work.** `modelscope.py`'s
   retry-broadening, the trailing-comma JSON repair, `verify.py`'s two-phase
   connection restructure (see below), and the Qwen-Ambassador model
   defaults are all sitting uncommitted right now — the last pushed commit
   is `88dd5d4`, itself still unpushed too.
3. **Decide Gemini-with-billing vs. real Anthropic API for the
   vision-dependent work** (tagging verification, `mcq-options`), then add
   that provider's path to `tag_from_crop` (currently ModelScope-only) —
   in progress, cost estimated for both, not yet implemented.
4. **Run `embed` for physics-5054.** Fully built, tested live, nothing
   blocking it — it just never got run. This alone would turn on the AI
   solver's real `match_question()` path instead of its fallback.
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
