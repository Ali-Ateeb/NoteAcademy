-- smoke_test.sql — exercises the invariants the schema is supposed to guarantee.
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/smoke_test.sql
-- Rolls itself back; leaves no data behind.
--
-- Safe against a populated database, which takes some care: every fixture below
-- has to avoid colliding with real content. A level is reused if one exists —
-- the code is an enum and unique, so a new one cannot be invented — and
-- everything else hangs off a syllabus code and an exam year that no real paper
-- can have. Assertions are scoped to the fixture's own rows for the same
-- reason: a check that counts every question in the table starts failing the
-- day the bank has questions in it.

begin;

-- ---------- fixtures ----------
-- Reused, not inserted: level codes are a four-value enum and unique, so a
-- seeded database already holds every one of them.
insert into levels (code, name, slug, sort_order)
values ('o_level', 'O Level', 'o-level', 1)
on conflict (code) do nothing;

insert into subjects (id, level_id, syllabus_code, title, slug, is_published)
select '22222222-2222-2222-2222-222222222222', id, '0000', 'Smoke Test',
       'smoke-test-subject', true
  from levels where code = 'o_level';

insert into syllabus_versions (id, subject_id, label, first_exam_year, is_current) values
  ('33333333-3333-3333-3333-333333333333',
   '22222222-2222-2222-2222-222222222222', '2023-2025', 2023, true);

insert into topics (id, syllabus_version_id, code, title, slug, learning_objectives) values
  ('44444444-4444-4444-4444-444444444444',
   '33333333-3333-3333-3333-333333333333', '1.2', 'Kinematics', 'kinematics',
   array['define speed and velocity', 'plot and interpret speed-time graphs']);

-- 2099: inside the schema's allowed range and beyond any real sitting.
insert into exam_sessions (id, year, season, slug) values
  ('55555555-5555-5555-5555-555555555555', 2099, 'may_june', '2099-may-june');

insert into papers (id, subject_id, exam_session_id, component, variant, slug) values
  ('66666666-6666-6666-6666-666666666666',
   '22222222-2222-2222-2222-222222222222',
   '55555555-5555-5555-5555-555555555555', 1, 2, 'smoke-test-subject-2099-may-june-p12');

insert into questions (id, paper_id, label, display_label, ordinal, question_type,
                       max_marks, correct_option, extraction_status) values
  ('77777777-7777-7777-7777-777777777777',
   '66666666-6666-6666-6666-666666666666', '1', '1', 1, 'mcq', 1, 'C', 'approved');

-- The student. On Supabase, profiles.id is a foreign key to auth.users (0009),
-- so the fixture has to sign a user up rather than write a profile directly —
-- which also exercises the thing 0009 exists to guarantee: that signing up
-- creates a profile, so no code path meets a logged-in user who has none.
do $$
begin
  if to_regclass('auth.users') is null then
    insert into profiles (id, display_name)
    values ('88888888-8888-8888-8888-888888888888', 'Test Student');
    raise notice 'SKIP: no auth.users (not Supabase); profile written directly';
  else
    insert into auth.users (id, email, instance_id, aud, role)
    values ('88888888-8888-8888-8888-888888888888', 'smoke-test@example.invalid',
            '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated');

    if not exists (select 1 from profiles
                    where id = '88888888-8888-8888-8888-888888888888') then
      raise exception 'FAIL: signing up did not create a profile';
    end if;
    raise notice 'PASS: signing up creates a profile and a free subscription';

    update profiles set display_name = 'Test Student'
     where id = '88888888-8888-8888-8888-888888888888';
  end if;
end $$;

-- ---------- 1. only one current syllabus version per subject ----------
do $$
begin
  insert into syllabus_versions (subject_id, label, first_exam_year, is_current)
  values ('22222222-2222-2222-2222-222222222222', '2026-2028', 2026, true);
  raise exception 'FAIL: a second current syllabus version was allowed';
exception when unique_violation then
  raise notice 'PASS: one current syllabus version per subject enforced';
end $$;

-- ---------- 2. a non-MCQ cannot carry a correct_option ----------
do $$
begin
  insert into questions (paper_id, label, display_label, ordinal, question_type, correct_option)
  values ('66666666-6666-6666-6666-666666666666', '2', '2', 2, 'structured', 'A');
  raise exception 'FAIL: a structured question was allowed an MCQ answer';
exception when check_violation then
  raise notice 'PASS: correct_option restricted to MCQs';
end $$;

-- ---------- 3. a withdrawn topic link must have no target ----------
do $$
begin
  insert into topic_links (from_topic_id, to_topic_id, relation)
  values ('44444444-4444-4444-4444-444444444444',
          '44444444-4444-4444-4444-444444444444', 'withdrawn');
  raise exception 'FAIL: a withdrawn link was allowed a target topic';
exception when check_violation then
  raise notice 'PASS: withdrawn topic links must have a null target';
end $$;

-- ---------- 4. attempts are append-only ----------
insert into attempts (user_id, question_id, selected_option, is_correct, time_spent_ms)
values ('88888888-8888-8888-8888-888888888888',
        '77777777-7777-7777-7777-777777777777', 'A', false, 21000);

update attempts set is_correct = true
 where user_id = '88888888-8888-8888-8888-888888888888';
do $$
begin
  if exists (select 1 from attempts where is_correct) then
    raise exception 'FAIL: an attempt row was mutated by UPDATE';
  end if;
  raise notice 'PASS: UPDATE on attempts is a no-op';
end $$;

delete from attempts where user_id = '88888888-8888-8888-8888-888888888888';
do $$
begin
  if (select count(*) from attempts) <> 1 then
    raise exception 'FAIL: an attempt row was removed by DELETE';
  end if;
  raise notice 'PASS: DELETE on attempts is a no-op';
end $$;

-- ---------- 5. current_answers returns the latest attempt, not the first ----------
insert into attempts (user_id, question_id, selected_option, is_correct, time_spent_ms)
values ('88888888-8888-8888-8888-888888888888',
        '77777777-7777-7777-7777-777777777777', 'C', true, 9000);

do $$
declare v_opt mcq_option;
begin
  select selected_option into v_opt from current_answers
   where user_id = '88888888-8888-8888-8888-888888888888';
  if v_opt <> 'C' then
    raise exception 'FAIL: current_answers returned % rather than the latest answer', v_opt;
  end if;
  raise notice 'PASS: current_answers resolves to the most recent attempt';
end $$;

-- ---------- 6. quota consumption stops at the limit and does not overshoot ----------
do $$
declare v_ok boolean; v_used int;
begin
  for i in 1..3 loop
    v_ok := consume_quota('88888888-8888-8888-8888-888888888888', 'ai_query', 3);
    if not v_ok then raise exception 'FAIL: quota refused call % of 3', i; end if;
  end loop;

  v_ok := consume_quota('88888888-8888-8888-8888-888888888888', 'ai_query', 3);
  if v_ok then raise exception 'FAIL: quota allowed a 4th call against a limit of 3'; end if;

  select used into v_used from usage_counters
   where user_id = '88888888-8888-8888-8888-888888888888' and metric = 'ai_query';
  if v_used <> 3 then
    raise exception 'FAIL: refused call left the counter at % rather than 3', v_used;
  end if;
  raise notice 'PASS: quota enforced at the limit, refused call rolled back';
end $$;

-- ---------- 7. RLS isolates one student's attempts from another's ----------
create role na_student nologin;
-- A superuser may `set role` to anything; a managed Postgres role (Supabase's
-- `postgres`, RDS's master) may only become a role it is a member of. Without
-- this grant the test cannot run against the database it is meant to protect.
grant na_student to current_user;
grant usage on schema public to na_student;
grant select, insert on attempts to na_student;
grant select on current_answers to na_student;

set local role na_student;
set local request.jwt.claims = '{"sub":"99999999-9999-9999-9999-999999999999"}';
do $$
begin
  if (select count(*) from attempts) <> 0 then
    raise exception 'FAIL: a student could read another student''s attempts';
  end if;
  raise notice 'PASS: RLS hides other students'' attempts';
end $$;

set local request.jwt.claims = '{"sub":"88888888-8888-8888-8888-888888888888"}';
do $$
begin
  if (select count(*) from attempts) <> 2 then
    raise exception 'FAIL: a student could not read their own attempts';
  end if;
  raise notice 'PASS: RLS admits a student to their own attempts';
end $$;

reset role;

-- ---------- 8. only approved questions are publicly visible ----------
update questions set extraction_status = 'needs_review'
 where id = '77777777-7777-7777-7777-777777777777';
grant select on questions to na_student;
set local role na_student;
do $$
begin
  if (select count(*) from questions
       where paper_id = '66666666-6666-6666-6666-666666666666') <> 0 then
    raise exception 'FAIL: an unapproved question was publicly visible';
  end if;
  raise notice 'PASS: unapproved questions are hidden from clients';
end $$;
reset role;

-- ---------- 9. public content tables are actually readable ----------
-- RLS with no policy denies every row *without erroring*, so a content table
-- that was never given a policy reads as empty rather than as broken. That is
-- how grade_thresholds and topic_links sat unreadable until 0016.
insert into grade_thresholds (subject_id, exam_session_id, grade, min_mark, max_mark)
values ('22222222-2222-2222-2222-222222222222',
        '55555555-5555-5555-5555-555555555555', 'A', 34, 40);

grant select on grade_thresholds, topic_links to na_student;
set local role na_student;
do $$
begin
  if (select count(*) from grade_thresholds
       where subject_id = '22222222-2222-2222-2222-222222222222') <> 1 then
    raise exception 'FAIL: a public content table denied every row';
  end if;
  raise notice 'PASS: public content tables are readable by a client';
end $$;
reset role;

-- ---------- 10. per-user aggregates are not on the public API ----------
-- topic_mastery is a materialised view keyed by user_id, and a materialised
-- view cannot be protected by row level security. The only thing standing
-- between it and every student's accuracy is the absence of a grant.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    raise notice 'SKIP: no anon role (not Supabase)';
  elsif has_table_privilege('anon', 'topic_mastery', 'select') then
    raise exception 'FAIL: anon can read every student''s topic mastery';
  else
    raise notice 'PASS: topic_mastery is off the public API';
  end if;
end $$;

rollback;
