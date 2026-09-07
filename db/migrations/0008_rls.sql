-- 0008_rls.sql
-- Row level security.
--
-- Supabase-shaped: auth.uid() identifies the authenticated user. Content tables
-- stay readable by everyone (the public paper index is the SEO surface and has
-- to be crawlable); everything user-owned is locked to its owner. The pipeline
-- writes with the service role, which bypasses RLS entirely.

-- Portability shim. On Supabase auth.uid() already exists and this is a no-op.
-- On plain Postgres it defines an equivalent backed by a session setting, so the
-- same policies can be exercised in tests:
--   set local request.jwt.claims = '{"sub":"<uuid>"}';
do $$
begin
  create schema if not exists auth;

  if to_regprocedure('auth.uid()') is null then
    create function auth.uid() returns uuid
    language sql stable
    as $fn$
      select nullif(
        current_setting('request.jwt.claims', true)::jsonb ->> 'sub', ''
      )::uuid;
    $fn$;
  end if;
end
$$;

alter table profiles            enable row level security;
alter table practice_sessions   enable row level security;
alter table attempts            enable row level security;
alter table subscriptions       enable row level security;
alter table payment_submissions enable row level security;
alter table usage_counters      enable row level security;

create policy profiles_self_read on profiles
  for select using (id = auth.uid());
create policy profiles_self_write on profiles
  for update using (id = auth.uid()) with check (id = auth.uid());

create policy practice_sessions_owner on practice_sessions
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Insert and select only: the no-update/no-delete rules in 0005 already make the
-- log immutable, and the policy set mirrors that rather than contradicting it.
create policy attempts_owner_read on attempts
  for select using (user_id = auth.uid());
create policy attempts_owner_insert on attempts
  for insert with check (user_id = auth.uid());

create policy subscriptions_owner_read on subscriptions
  for select using (user_id = auth.uid());

create policy payment_submissions_owner_read on payment_submissions
  for select using (user_id = auth.uid());
create policy payment_submissions_owner_insert on payment_submissions
  for insert with check (user_id = auth.uid());

create policy usage_counters_owner_read on usage_counters
  for select using (user_id = auth.uid());

-- Content tables: readable by anyone, writable only by the service role.
alter table levels            enable row level security;
alter table subjects          enable row level security;
alter table syllabus_versions enable row level security;
alter table topics            enable row level security;
alter table exam_sessions     enable row level security;
alter table papers            enable row level security;
alter table paper_documents   enable row level security;
alter table questions         enable row level security;
alter table question_assets   enable row level security;
alter table question_topics   enable row level security;

create policy levels_public            on levels            for select using (true);
create policy subjects_public          on subjects          for select using (is_published);
create policy syllabus_versions_public on syllabus_versions for select using (true);
create policy topics_public            on topics            for select using (true);
create policy exam_sessions_public     on exam_sessions     for select using (true);
create policy papers_public            on papers            for select using (true);
create policy question_assets_public   on question_assets   for select using (true);
create policy question_topics_public   on question_topics   for select using (true);

-- Only approved questions are ever visible to clients. Rows still being
-- extracted or awaiting review are invisible outside the service role, so a
-- half-parsed question can never leak into the topical engine or the arena.
create policy questions_public on questions
  for select using (extraction_status = 'approved');

-- Documents are listed publicly (the viewer needs to know a mark scheme exists)
-- but storage_key alone grants nothing: the app hands out short-lived signed
-- URLs, it never exposes the bucket.
create policy paper_documents_public on paper_documents for select using (true);
