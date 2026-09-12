-- 0024_secure_current_answers.sql
-- current_answers has carried the same gap since 0005 that 0016 found and
-- fixed for topic_mastery: Supabase grants every new relation in `public` to
-- anon and authenticated in full at creation, and a plain view with no
-- security_invoker runs as its owner rather than as the caller — so a view
-- over a table protected by row level security actually bypasses that
-- protection. Confirmed live: anon and authenticated both still hold SELECT
-- on current_answers, which would return every student's current answer to
-- every question, not just the caller's own. Nothing has leaked because
-- attempts has been empty since the table was created — that is timing, not
-- protection, the same distinction 0016 drew.
--
-- Fixed the way this codebase already fixes this exact shape (v_papers,
-- v_structured_questions): security_invoker makes the view subject to
-- attempts' own RLS (attempts_owner_read: user_id = auth.uid()) instead of
-- superseding it, so plain `select * from current_answers` as an
-- authenticated user now returns only that user's own rows with no extra
-- filter needed. anon is not granted at all — auth.uid() is null for an
-- anonymous request, so anon could only ever match zero rows anyway, and not
-- granting says so rather than relying on that being true.
alter view current_answers set (security_invoker = true);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on current_answers from anon, authenticated;
    grant select on current_answers to authenticated;
  end if;
end
$$;
