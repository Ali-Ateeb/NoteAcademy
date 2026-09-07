# Database

PostgreSQL 16+ with `pgvector`. Migrations are plain SQL, applied in filename
order. There is no migration tool wired up yet on purpose — pick one (`dbmate`,
`atlas`, Supabase CLI) once the schema stops moving weekly.

## Applying

```bash
for f in db/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`0009_supabase_auth.sql` is **Supabase-only** — it references `auth.users`, which
does not exist on plain Postgres. Skip it locally; the rest of the schema does
not depend on it.

## Verifying

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/smoke_test.sql
```

Checks the invariants the application relies on and then rolls back, so it is
safe against a populated database. Every check prints `PASS` or aborts.

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

**Quota is consumed inside the database.** `consume_quota()` bumps and checks in
one statement and rolls its own increment back on refusal, so concurrent requests
cannot both pass a check-then-increment race.

## Local Postgres with pgvector

```bash
apt-get install -y postgresql-16 postgresql-16-pgvector   # Debian/Ubuntu
createdb noteacademy
export DATABASE_URL=postgresql://postgres@localhost:5432/noteacademy
```
