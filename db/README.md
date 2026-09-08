# Database

PostgreSQL 16+ with `pgvector`. Migrations are plain SQL, applied in filename
order. There is no migration tool wired up yet on purpose — pick one (`dbmate`,
`atlas`, Supabase CLI) once the schema stops moving weekly.

## Applying

```bash
python db/apply.py               # 0001-0008 and 0010+, the portable schema
python db/apply.py --supabase    # ... and 0009, which needs auth.users
python db/apply.py --status      # what is applied; changes nothing
```

`psql -f` still does the same thing and is the documented path elsewhere:

```bash
for f in db/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`apply.py` exists because it is the one route that works everywhere this is
developed — Windows has no `psql` unless PostgreSQL is installed locally, and
the migrations have to reach a hosted Supabase database either way. It is a
ledger, not a framework: a migration is a file, applied once, recorded in
`schema_migrations` with its checksum. Editing a file that has already run is
refused rather than silently diverging from the database it claims to describe.
Write a new migration instead.

`0009_supabase_auth.sql` is **Supabase-only** — it references `auth.users`, which
does not exist on plain Postgres. Skip it locally; the rest of the schema does
not depend on it.

## Seeding

```bash
psql "$DATABASE_URL" -f db/seed_catalog.sql     # or run it through apply.py's loader
```

Levels, subjects and the syllabus version. Not fixtures — the pipeline resolves
a subject *slug* before it can load a paper, so a database without these rows
rejects every ingestion run. It is idempotent and never resets `is_published`:
publishing a subject is a decision made once its ingestion is actually done.

The topic tree is deliberately not seeded. It has to be transcribed from the
published syllabus, and a topic tree that is nearly right is worse than none —
it mistags the bank and sends students to revise the wrong unit.

## Verifying

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/smoke_test.sql
```

Checks the invariants the application relies on and then rolls back, so it is
safe against a populated database. Every check prints `PASS` or aborts.

Run it **before** `0009`. It inserts a profile row directly, and 0009 ties
`profiles.id` to `auth.users`, so on Supabase with 0009 already applied the
fixtures fail the foreign key.

## The parts worth knowing before you change anything

**Topics hang off a syllabus version, never off a subject.** CAIE revises
syllabuses, so a topic tree is only meaningful relative to a version, and a 2014
question may test an outcome that has since been withdrawn. `topic_links` maps
nodes between versions so a student on the current syllabus can be shown older
questions without being served content that no longer appears.

**A paper is the sitting; its QP, MS, ER and thresholds are documents attached to
it.** Putting `doc_type` on `papers` — the common shortcut — makes a question
paper and its mark scheme unrelated rows with nothing to join on, which is
exactly the join the split-screen viewer needs.

**`attempts` is append-only and enforced as such.** `UPDATE` and `DELETE` are
rewritten to no-ops by rule. A student changing their answer inserts a new row;
"their answer" is the highest `seq`. Order by `seq`, never `created_at` — `now()`
is the transaction timestamp, so rows written together share it exactly.

**Nothing reaches a student until it is approved.** The pipeline writes
`extraction_status = 'extracted'`; the review queue promotes rows to `approved`.
RLS only exposes approved questions, so a half-parsed question cannot leak into
the topical engine or the practice arena even if application code forgets to
filter.

**The site reads views, not tables.** `db/migrations/0012_read_views.sql`
defines one view per page-level question, and the web app selects from those.
The reason is not tidiness: PostgREST's embedded aggregates count rows visible
in the embedded table rather than rows that survive the join, so a topic's
question count assembled in the client silently includes questions still waiting
on review. Every view is `security_invoker`, so row level security still applies
through it — a view is otherwise checked with its owner's rights, which would
serve unapproved questions to anyone who asked.

**Quota is consumed inside the database.** `consume_quota()` bumps and checks in
one statement and rolls its own increment back on refusal, so concurrent requests
cannot both pass a check-then-increment race.

## Local Postgres with pgvector

```bash
apt-get install -y postgresql-16 postgresql-16-pgvector   # Debian/Ubuntu
createdb noteacademy
export DATABASE_URL=postgresql://postgres@localhost:5432/noteacademy
```
