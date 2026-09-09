-- 0021_revoke_review_decided.sql
-- 0020 declined to grant v_review_decided to anon/authenticated and said so in
-- a comment, which — as 0013 already found for v_review_queue, and 0018/0019
-- found for the content views — is not the same thing as withholding it.
-- Supabase's default privileges grant every new table and view in `public` to
-- anon and authenticated as it is created, so the view answered anonymous
-- requests despite the comment saying otherwise. Row level security on the
-- underlying `questions` table still limited what came back — a rejected
-- question is unapproved by definition — but that is the same "resting on a
-- second mechanism" gap this whole session has been closing elsewhere, and it
-- should not have shipped here too. Revoked explicitly, matching 0013.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_review_decided from anon, authenticated;
  end if;
end
$$;
