-- 0030_attempts_allow_cascade.sql
-- Let a profile (and its questions) actually be deleted; keep attempts
-- append-only against direct writes.
--
-- 0005 made attempts immutable with two RULEs: `do instead nothing` on
-- UPDATE and DELETE. A RULE rewrites the query itself, before the planner
-- ever sees it as a delete -- including the DELETE that Postgres's own
-- ON DELETE CASCADE machinery issues against attempts when a profile (or a
-- question) is removed. That confuses the FK's referential-integrity check,
-- which expects the cascade delete it just ran to have actually happened:
--
--   psycopg.errors.InternalError_: referential integrity query on "profiles"
--   from constraint "attempts_user_id_fkey" on "attempts" gave unexpected result
--   HINT:  This is most likely due to a rule having rewritten the query.
--
-- The result is that no profile with an attempts foreign key pointing at it
-- can ever be deleted -- not through `delete from profiles`, not through
-- `auth.users` cascading into it -- even when that profile has zero attempts.
-- That blocks every account-deletion path: user-initiated, GDPR/CCPA erasure,
-- and routine test-data cleanup.
--
-- The actual invariant 0005 wants is "an application can't rewrite history",
-- not "a profile can never be removed" -- once the profile (or question) is
-- gone, there is no history left to protect. So: replace the RULEs with
-- BEFORE triggers that check pg_trigger_depth(). A direct `update`/`delete`
-- on attempts fires this trigger at depth 1 (it's the only trigger on the
-- stack) and gets rejected. A write that arrives because Postgres's FK action
-- triggers are cascading a delete on profiles/questions (ON DELETE CASCADE)
-- or a delete on practice_sessions (ON DELETE SET NULL on
-- practice_session_id) runs from *inside* that FK trigger, so this one fires
-- at depth 2 and is allowed through.
drop rule if exists attempts_no_update on attempts;
drop rule if exists attempts_no_delete on attempts;

create or replace function attempts_append_only()
returns trigger
language plpgsql
as $$
begin
  if pg_trigger_depth() > 1 then
    return coalesce(new, old);
  end if;

  -- restrict_violation, not the default raise_exception: gives callers (and
  -- the smoke test) a stable SQLSTATE to catch instead of matching on text.
  raise exception 'attempts is append-only: % is not allowed directly (record a new attempt instead)', tg_op
    using errcode = 'restrict_violation';
end;
$$;

create trigger attempts_append_only_update
  before update on attempts
  for each row execute function attempts_append_only();

create trigger attempts_append_only_delete
  before delete on attempts
  for each row execute function attempts_append_only();
