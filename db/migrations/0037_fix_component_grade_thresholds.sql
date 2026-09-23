-- 0037_fix_component_grade_thresholds.sql
-- Cambridge's own "Component 11" label is not a single digit -- it is a
-- paper's component and variant printed together (component 1, variant 1),
-- the same pair `papers.component`/`papers.variant` already store
-- separately. 0036 copied `papers.component`'s own check constraint
-- (between 1 and 9) onto this table's `component` column without adjusting
-- for that, so the first real load caught it immediately: inserting
-- component 11 tripped a check constraint built for single digits. Nothing
-- ever committed (this table has held zero rows since 0036 created it), so
-- this drops and recreates it with the right shape rather than patching
-- around the mistake.
drop table component_grade_thresholds;

create table component_grade_thresholds (
  id              uuid primary key default gen_random_uuid(),
  subject_id      uuid not null references subjects(id) on delete cascade,
  exam_session_id uuid not null references exam_sessions(id) on delete cascade,
  component       int  not null check (component between 1 and 9),
  variant         int  check (variant between 0 and 9),
  grade           text not null,
  min_mark        int  not null,
  max_mark        int  not null,
  unique (subject_id, exam_session_id, component, variant, grade)
);

alter table component_grade_thresholds enable row level security;

create policy component_grade_thresholds_public on component_grade_thresholds
  for select using (true);
