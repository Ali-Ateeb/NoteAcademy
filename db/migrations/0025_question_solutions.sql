-- 0025_question_solutions.sql
-- A worked solution for a question, generated once and reused forever after.
--
-- The syllabus content of a question does not change between the first
-- student who asks for a solution and the ten-thousandth, so paying an LLM
-- call per request rather than per question would scale cost with usage for
-- no benefit. One row per question, written the first time anyone asks.
--
-- Same content gate as question_options: a solution is derived from the
-- question, so it stays invisible until the question itself is approved.

create table question_solutions (
  question_id  uuid not null references questions(id) on delete cascade,
  solution     text not null,
  model        text not null,
  created_at   timestamptz not null default now(),
  primary key (question_id)
);

alter table question_solutions enable row level security;

create policy question_solutions_public on question_solutions
  for select using (
    exists (
      select 1 from questions q
      where q.id = question_solutions.question_id
        and q.extraction_status = 'approved'
    )
  );
