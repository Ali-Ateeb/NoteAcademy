-- 0013_revoke_review_queue.sql
-- Take the reviewer queue off the public API surface.
--
-- 0012 declined to grant it to Supabase's API roles, which turned out not to be
-- the same thing as withholding it: Supabase carries default privileges that
-- grant new tables and views in `public` to anon and authenticated as they are
-- created, so the view answered anonymous requests. It answered them with an
-- empty array — row level security still applies, and every row in that view is
-- unapproved by definition — but relying on that leaves the queue one policy
-- edit away from being readable. Revoke it, so the protection does not depend on
-- a second mechanism agreeing.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_review_queue from anon, authenticated;
  end if;
end
$$;
