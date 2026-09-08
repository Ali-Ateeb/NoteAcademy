-- 0016_close_the_gaps.sql
-- Five tables 0008 did not account for, found by auditing the live database
-- rather than the migration files.
--
-- Two classes of problem, and they fail in opposite directions.

-- ---------------------------------------------------------------------------
-- 1. topic_mastery: a materialised view of every student's per-topic accuracy.
--
-- Row level security cannot be applied to a materialised view, and this one is
-- keyed by user_id. Supabase grants new relations in `public` to the API roles
-- as they are created, so it was reachable with the publishable key — which
-- means every student's accuracy, timings and last-attempt dates, for anyone
-- who asked. Nothing has leaked because no attempt has ever been written, but
-- that is timing, not protection.
--
-- Revoked rather than wrapped. When attempts move server-side, expose it
-- through a view that filters on auth.uid() explicitly; a matview cannot be
-- protected by policy, so the filter has to be written into the query.
revoke all on topic_mastery from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on topic_mastery from anon, authenticated;
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. Content tables 0008 missed, which now fail closed and silently.
--
-- `grade_thresholds` and `topic_links` are public content by the same argument
-- as every other content table: the paper index is the acquisition channel and
-- has to be crawlable. On Supabase they have row level security enabled (new
-- tables in `public` get it) and no policy at all, which denies every row
-- without erroring — so the first page to read a grade boundary would render
-- empty and nothing would say why. On plain Postgres they have no row level
-- security, so the same two tables are wide open. Both halves are fixed here.
alter table grade_thresholds enable row level security;
alter table topic_links      enable row level security;

drop policy if exists grade_thresholds_public on grade_thresholds;
drop policy if exists topic_links_public      on topic_links;

create policy grade_thresholds_public on grade_thresholds for select using (true);
create policy topic_links_public      on topic_links      for select using (true);

-- ---------------------------------------------------------------------------
-- 3. Tables that are correctly closed, said out loud.
--
-- The retrieval index and the migration ledger have no policy because nothing
-- outside the service role should read them: embeddings reconstruct the corpus
-- they were built from, and the ledger describes the schema to an attacker.
-- They were already denied by an absent policy; revoking says so on purpose,
-- so the protection does not rest on a second mechanism continuing to agree.
alter table content_chunks      enable row level security;
alter table question_embeddings enable row level security;
alter table schema_migrations   enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on content_chunks, question_embeddings, schema_migrations
      from anon, authenticated;
  end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 4. Pin the search path on our own functions.
--
-- Neither is SECURITY DEFINER, so this is not the privilege-escalation case —
-- but a function that resolves `usage_counters` through whatever search_path
-- its caller happens to have set is resolving a table name at the caller's
-- discretion, and quota enforcement is the wrong place for that.
alter function consume_quota(uuid, text, int, int) set search_path = public, pg_temp;
alter function match_question(vector, uuid, real, int) set search_path = public, pg_temp;
