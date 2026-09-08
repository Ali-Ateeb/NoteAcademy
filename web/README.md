# Web app

Next.js App Router, TypeScript, Tailwind v4. One deploy target — API routes and
server components do the backend work, so there is no separate service to run.

```bash
npm install
npm run dev            # works immediately: no database, no API keys
```

To point it at a real backend, copy `web/.env.example` to `web/.env.local` and
fill it in. **Next.js reads `.env.local` from this directory only** — a `.env` at
the repository root configures the database and pipeline and is never read by the
web app.

`SUPABASE_SERVICE_ROLE_KEY` bypasses row level security. Never prefix it
`NEXT_PUBLIC_` and never import it into a client component; it belongs in server
components and route handlers alone.

The app reads seed fixtures when `NEXT_PUBLIC_SUPABASE_URL` is unset, so a fresh
clone is a working site. `src/lib/data/catalog.ts` is the only file that changes
when real content arrives — every function there becomes a query and no page
component is touched.

> The seed questions in `src/lib/data/seed.ts` were **written for this scaffold**.
> They are not Cambridge past-paper questions and must not be presented as such.

## Checks

```bash
npm run typecheck
npm run lint
npm run build
npm run start -- -p 3210 &   # then, in another shell:
npm run e2e
```

`e2e/smoke.mjs` drives a real browser through the behaviour that unit tests
cannot reach and that breaks quietly: the timer ticking, a half-finished paper
surviving a reload, marking against the answer key, and the dashboard reading the
attempt log. Set `CHROMIUM_PATH` if Playwright's browser lives somewhere unusual.

## Routes

| Route | What it is |
|---|---|
| `/` | Landing |
| `/subjects` | Level → subject directory |
| `/subjects/[subject]` | Papers by year, topics, playable papers |
| `/papers/[paper]` | Split-screen viewer: QP beside MS / ER |
| `/practice/[paper]` | Timed, resumable MCQ arena |
| `/topics/[subject]/[topic]` | Topical questions with mark schemes |
| `/topics/[subject]/[topic]/practice` | Untimed drill of one topic |
| `/dashboard` | Accuracy per topic, worst first |
| `/admin/review` | Review queue for questions the pipeline held back |

Everything except `/dashboard` is statically generated. That is not an
optimisation — "5054 may june 2019 paper 12" is a real search query, and tens of
thousands of indexable paper pages is the entire acquisition channel for a
product like this.

## Things that are load-bearing

**Base styles live in `@layer base`.** Tailwind's utilities sit in a cascade
layer, and unlayered rules beat every layered rule regardless of specificity. An
unlayered `* { border-color: ... }` silently kills `border-accent`,
`border-line-strong` and every other border-colour utility in the codebase. This
bug is invisible until you inspect computed styles.

**Colours are defined once on `:root`** and only redefined under
`prefers-color-scheme: dark` and `[data-theme="dark"]`. No colour has its only
definition inside a media query, so the explicit toggle wins in both directions.
The theme is applied by an inline script in `<head>` before first paint, which is
why there is no flash of the wrong theme.

**The MCQ timer depends on `running`, never on `state`.** Depending on the state
object tears down and recreates the interval on every keystroke, so a student
answering quickly watches a frozen clock. It shipped that way once; `e2e/smoke.mjs`
now checks it.

**The attempt log is append-only and ordered by `seq`,** mirroring the `attempts`
table exactly. That is what lets a signed-out student's local history migrate
into their account on first login instead of being thrown away, and it is why
`currentAnswers()` can resolve "their answer" unambiguously.

**The clock does not run while the tab is closed.** Losing connectivity should
never cost a student the paper.

**Topic drills are untimed, and papers are timed.** Drilling a weak topic is
about getting it right; a countdown adds pressure to precisely the thing the
student is already worst at. `McqArena` takes `secondsPerQuestion={null}` for
drills, and a distinct `sessionKey`, so a drill can never overwrite a
half-finished exam. `e2e/smoke.mjs` checks that specifically.

**The review queue is keyboard-only by design.** A real backfill puts thousands
of questions through `/admin/review`, so every decision is one keystroke (A, R,
U, J/K, 1–9) and the queue is ordered worst-confidence first — attention goes
where the pipeline is least sure, not in page order. Each item names the specific
check that held it back, which is what makes a queue that size tractable.
