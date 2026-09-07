-- 0005_attempts.sql
-- Users, practice sessions, and the attempt event log.

create table profiles (
  id           uuid primary key,            -- on Supabase: = auth.users.id (see 0009)
  display_name text,
  email        text,
  country      text,
  -- The syllabus version the student is sitting. Drives which topics and which
  -- historical questions they are shown.
  target_level_id uuid references levels(id),
  target_session_year int,
  created_at   timestamptz not null default now()
);

create type practice_mode as enum ('paper_mcq', 'topical', 'custom_drill');
create type practice_state as enum ('in_progress', 'submitted', 'abandoned');

-- One row per sitting. `question_ids` freezes the question order at creation so
-- a resumed test is identical to the one the student started, even if the bank
-- changes underneath them.
create table practice_sessions (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references profiles(id) on delete cascade,
  mode           practice_mode not null,
  subject_id     uuid references subjects(id) on delete set null,
  paper_id       uuid references papers(id) on delete set null,
  question_ids   uuid[] not null,
  duration_seconds int,                      -- null = untimed
  state          practice_state not null default 'in_progress',
  -- Client-owned scratch state: current index, flags, scratchpad text. Kept as
  -- jsonb so the arena can evolve without a migration per feature.
  ui_state       jsonb not null default '{}'::jsonb,
  started_at     timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  submitted_at   timestamptz
);

create index practice_sessions_user_idx on practice_sessions (user_id, started_at desc);
create index practice_sessions_resume_idx on practice_sessions (user_id)
  where state = 'in_progress';

-- Append-only. Never UPDATE a row here: a student changing their answer writes a
-- new attempt, and "their answer" is the latest row. Every analytic in the
-- product is a view over this table, so keeping it immutable means any metric
-- can be recomputed from history rather than migrated.
create table attempts (
  id                  uuid primary key default gen_random_uuid(),
  -- Monotonic insertion order. created_at cannot resolve "which answer is the
  -- student's latest": now() is the *transaction* timestamp, so two attempts
  -- written in one transaction carry an identical value and the ordering
  -- between them is undefined. seq is the tiebreak that makes it total.
  seq                 bigserial not null unique,
  user_id             uuid not null references profiles(id) on delete cascade,
  question_id         uuid not null references questions(id) on delete cascade,
  practice_session_id uuid references practice_sessions(id) on delete set null,
  selected_option     mcq_option,
  is_correct          boolean,
  -- Self-marked confidence for structured questions, where there is no
  -- machine-checkable answer: 0 = got nothing, 1 = full marks.
  self_marked_score   real check (self_marked_score between 0 and 1),
  time_spent_ms       int check (time_spent_ms >= 0),
  -- clock_timestamp(), not now(): the wall-clock instant of this row, so a batch
  -- of answers submitted together still carries distinct times.
  created_at          timestamptz not null default clock_timestamp()
);

create index attempts_user_idx     on attempts (user_id, seq desc);
create index attempts_question_idx on attempts (question_id);
create index attempts_session_idx  on attempts (practice_session_id);

create rule attempts_no_update as on update to attempts do instead nothing;
create rule attempts_no_delete as on delete to attempts do instead nothing;

-- Latest attempt per (user, question) — "their current answer".
create view current_answers as
select distinct on (user_id, question_id)
  user_id, question_id, selected_option, is_correct, self_marked_score,
  time_spent_ms, created_at, seq
from attempts
order by user_id, question_id, seq desc;

-- Per-topic accuracy: the weak-area heatmap on the dashboard. Refresh nightly,
-- or on submit for the session's own topics.
create materialized view topic_mastery as
select
  ca.user_id,
  qt.topic_id,
  count(*)                                             as attempted,
  count(*) filter (where ca.is_correct)                as correct,
  avg(ca.is_correct::int)::real                        as accuracy,
  percentile_cont(0.5) within group (order by ca.time_spent_ms)::int as median_ms,
  max(ca.created_at)                                   as last_attempt_at
from current_answers ca
join question_topics qt on qt.question_id = ca.question_id
where ca.is_correct is not null
group by ca.user_id, qt.topic_id;

create unique index topic_mastery_pk on topic_mastery (user_id, topic_id);
