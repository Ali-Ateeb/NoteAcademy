-- 0019_revoke_view_write_grants.sql
-- v_papers, v_subjects and v_topics were created by 0012 with an explicit
-- `grant select ... to anon, authenticated`, but that grant only *adds* to
-- whatever a role already holds — it never revoked Supabase's default (every
-- new relation in `public` is granted in full to the API roles at creation),
-- so anon and authenticated have carried INSERT/UPDATE/DELETE/TRUNCATE/
-- REFERENCES/TRIGGER on all three views since 0012 first ran. v_mcq_questions
-- had the identical problem, found and fixed in 0018; this is the rest of it,
-- found by the same live audit rather than by reading the migration files.
--
-- Currently inert: none of these views is a simple, automatically-updatable
-- view (each joins or aggregates), so Postgres refuses any write through them
-- regardless of the grant, and row level security on the underlying tables
-- would refuse it a second time even if it were. But that is two mechanisms
-- agreeing, not one being true — the same distinction 0016 drew for
-- topic_mastery and the retrieval tables. Said out loud and made explicit
-- here rather than left to keep agreeing by accident.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_papers, v_subjects, v_topics from anon, authenticated;
    grant select on v_papers, v_subjects, v_topics to anon, authenticated;
  end if;
end
$$;
