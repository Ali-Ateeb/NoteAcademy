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
- Both `extract.py` (vision) and `tagging.py` (direct-API tagging, still
  otherwise unused — see below) can now run against Qwen via ModelScope's
  API-Inference endpoint instead of Gemini, switched independently per stage
  by `NOTEACADEMY_EXTRACTION_PROVIDER` / `NOTEACADEMY_TAGGING_PROVIDER`.
  Added after Gemini's persistent `503` this session, with real ModelScope
  credits sitting unused. Not yet run for real — needs `MODELSCOPE_API_KEY`,
  which isn't in `.env` yet. The endpoint itself is confirmed live and
  reachable (a deliberately-invalid token gets a clean 401, not a connection
  or routing failure).
- `mcq-options` (vision backfill of MCQ question/option text) and `embed`
  (retrieval embeddings) are both duplicate- and idempotency-aware: a
  question already backfilled, or a verbatim duplicate of one that is,
  costs nothing to re-run or skip. Neither has been run at scale yet — see
  Known gaps.
- 30 migrations, all applied to the live database with matching checksums
  (`db/apply.py --status`), including `0030` fixing the attempts/profiles
  cascade-delete bug.

---

## Known gaps

Concrete and verified this session, not carried forward from an old list:

- **Zero reviewer accounts provisioned.** `profiles.is_reviewer = true` has
  never been set on a real account in production. `/admin/review` is gated
  correctly, but nobody can currently pass the gate.
- **Item #7 is still open, and both attempts to close it this session hit an
  external blocker, not a code problem:**
  - `mcq-options` against physics-5054 hit a persistent `503 UNAVAILABLE
    ("high demand")` from Gemini on `gemini-3.6-flash`, across four attempts
    over several minutes. Very likely transient upstream capacity, not
    anything wrong here — worth retrying.
  - `embed` against physics-5054 can't run at all: `VOYAGE_API_KEY` is
    present as a key in `.env` but its value is empty. Needs a real key from
    voyageai.com before this can run for real. (Not blocked on Gemini at
    all, and not blocked on `mcq-options` either — confirmed live that all
    594 MCQs and 667 structured questions already resolve non-empty
    embedding text straight from the PDF text layer, with zero model calls.)
  - `question_options` and `question_embeddings` are still both 0 rows.
    Most MCQs display as a crop image with no selectable text, and the AI
    solver still can't use `match_question()`.
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

1. ~~**Fix the `/auth/callback` open redirect.**~~ Done — `safeRedirect.ts`,
   see Live today.
2. ~~**Commit and push everything outstanding.**~~ Done — the attempts/
   profiles cascade-delete fix, the pipeline efficiency work, and the
   open-redirect fix landed as three separate commits and are pushed.
3. **Get a real `VOYAGE_API_KEY` into `.env`, then run `embed` for
   physics-5054.** Nothing else is blocking this one — it's ready to run the
   moment a key exists.
4. **Get a real `MODELSCOPE_API_KEY` into `.env`, confirm the two Qwen model
   IDs against ModelScope's current catalog, then retry `mcq-options` for
   physics-5054 with `NOTEACADEMY_EXTRACTION_PROVIDER=modelscope`.** This is
   the same item #7 work as #3, just the vision half — routed off Gemini
   specifically because that's the half that hit the `503`.
5. **Provision at least one reviewer account** (`update profiles set
   is_reviewer = true where email = '...'`). Costs nothing and unblocks the
   review queue, which currently has no one who can sign into it.
6. **Patch the `/api/solve` quota-refund gap.** Small, self-contained, and
   the failure mode (silently charging a student for a solve that never
   happened) is the kind of thing that erodes trust quietly.
7. **Extend tagging verification to chemistry-5070, biology-5090, and
   physics's own structured bank.** This is the actual accuracy lever — not
   a token-cost problem, since the verification machinery (`tag-verify-export`
   / `tag-verify-apply`) already exists and was run once at zero API cost. It
   costs review time, and 80% of the tagged corpus is currently unverified.
   `tag_question` now running on Qwen (see #4) is a real option here too —
   it's a genuine second, independent classifier, which is exactly what a
   verification pass needs, and it's text-only so it doesn't need the
   vision-capable model or wait on Gemini at all.
8. **Upload the source PDFs and finish the split viewer.** Unlocks the
   `SplitViewer`'s real panes instead of placeholders.
9. **Payments**, once there's a live audience to charge.
