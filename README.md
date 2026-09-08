# NoteAcademy

A revision platform for Cambridge (CAIE) students: past papers split into
individual questions, tagged to the syllabus version you are actually sitting,
with the mark scheme and examiner report attached to each one.

```
├── web/       Next.js app — the whole front end and its API routes
├── db/        PostgreSQL schema as plain SQL migrations, plus a smoke test
├── pipeline/  Python batch jobs that turn paper PDFs into the question bank
├── scripts/   Standalone tools; fetch_papers.py downloads the source PDFs
└── docs/      Architecture notes and the build order
```

## Running it

```bash
cd web && npm install && npm run dev
```

That is the whole setup. The app runs against seed fixtures with no database and
no API keys, so a fresh clone is a working site. See `web/README.md` for checks,
`db/README.md` for the schema, and `pipeline/README.md` for ingestion.

With a database configured it reads that instead, and the fixtures are never
mixed in: a configured database that errors raises rather than quietly serving
invented questions under the banner of real past papers.

```bash
python db/apply.py --supabase                 # schema
psql "$DATABASE_URL" -f db/seed_catalog.sql   # levels, subjects, syllabus version
python scripts/fetch_papers.py 5054 --components 1   # source PDFs
noteacademy load-syllabus 5054-syllabus.pdf   # the topic tree
noteacademy load-mcq papers/5054/5054_s19_qp_11.pdf papers/5054/5054_s19_ms_11.pdf
```

Nothing from that last step is visible to anyone yet — it lands unapproved, and
`is_published` still gates the subject. That is the design, not a missing step.

## Where the keys go

**Two files, in two places.** They are not interchangeable: Next.js reads
`.env.local` from `web/` and never looks at the repository root, so a key put in
the wrong file silently does nothing.

| File | Configures | Copy from |
|---|---|---|
| `web/.env.local` | The web app — Supabase URL and keys | `web/.env.example` |
| `.env` (repo root) | Database, ingestion pipeline, storage | `.env.example` |

Both are gitignored. A real environment variable always overrides the file, so
CI and production set variables directly and never ship a `.env`.

**You need very little to start.** `DATABASE_URL` plus the two
`NEXT_PUBLIC_SUPABASE_*` values are enough to run against a real database.
`ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` are not needed for multiple-choice
ingestion at all — that path is fully deterministic — only for structured papers,
topic tagging and the solver. Storage keys can wait until the viewer serves real
documents.

**One rule worth stating plainly:** `SUPABASE_SERVICE_ROLE_KEY` bypasses row
level security completely. It must never be prefixed `NEXT_PUBLIC_` and never
imported into a client component — in a browser bundle it grants every visitor
full read and write on every table. The anon key is the opposite: it is designed
to be public, and RLS (`db/migrations/0008_rls.sql`) is what actually protects
the data.

## The thing to understand first

The tech stack is not the hard part. Next.js, Postgres and an LLM are a solved
problem you could stand up in a week. **The ingestion pipeline and the quality of
the derived data are the entire product.** Every competitor has the same PDFs.
Almost none of them have accurately segmented, correctly tagged,
mark-scheme-linked questions — and that is roughly 90% of the engineering effort
here.

Two consequences shape everything in this repo:

**Depth beats breadth.** One subject with every question correctly tagged is
worth more than forty subjects at 70% accuracy, because a wrong topic tag
actively wastes the revision time it was supposed to save. `subjects.is_published`
exists so a subject stays invisible until its bank has been reviewed.

**Nothing reaches a student unreviewed.** The pipeline writes questions as
`extracted` or `needs_review`; row-level security exposes only `approved`. A
half-parsed question cannot leak into the topical engine or the practice arena
even if application code forgets to filter.

## Build order

1. **Paper browsing and the split-screen viewer**, one subject, statically generated
2. **The MCQ arena** — highest value for the least effort, because MCQ mark
   schemes are answer grids with no segmentation problem at all. It also produces
   the attempt data that makes the dashboard non-empty on day one
3. **The topical engine** for that same subject
4. **The AI solver**, grounded on the now-complete mark-scheme index
5. **More subjects** — by this point the pipeline is repeatable
6. **Payments**

Steps 1, 2, 3 and 6's data model are scaffolded here. See `docs/architecture.md`
for the decisions behind them and `docs/roadmap.md` for what each remaining step
involves.

## A note on content

Cambridge Assessment International Education holds copyright in its question
papers, mark schemes and examiner reports. This repository contains none of that
material: the seed data was written for the scaffold and is labelled as such.
Sourcing and rights for real content are decisions for whoever operates the
platform, and the architecture keeps raw PDFs behind swappable signed-URL storage
so that the derived layer — segmentation, tagging, analytics, explanations —
remains the product surface.
