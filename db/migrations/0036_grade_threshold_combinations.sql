-- 0036_grade_threshold_combinations.sql
-- Fit the shape Cambridge actually publishes grade thresholds in.
--
-- Reading two real threshold PDFs (physics 5054 and chemistry 5070, June
-- 2024) before writing anything against this table showed it does not
-- match what Cambridge publishes. Each subject/session publishes thresholds
-- at two levels:
--
--   * per component (papers 11, 21, 31, ...): grades A-E only -- there is no
--     A* at the level of a single component.
--   * per *combination* of components: grades A*-E. A subject publishes
--     several combinations -- physics 5054 June 2024 has four (AX, AY, BX,
--     BY), chemistry 5070 June 2024 has six -- because which alternative
--     route a candidate took (e.g. practical vs alternative-to-practical)
--     changes which components make up their overall mark, and each
--     combination gets its own thresholds.
--
-- `grade_thresholds`' original `unique (subject_id, exam_session_id, grade)`
-- assumed one threshold set per subject per session. It cannot hold a
-- second combination's numbers without silently overwriting the first.
-- Nothing has ever been loaded into this table (it is empty), so this widens
-- it in place rather than migrating data.
alter table grade_thresholds
  drop constraint grade_thresholds_subject_id_exam_session_id_grade_key;

alter table grade_thresholds
  add column combination text not null,
  add column components  text not null;  -- e.g. '11,21,31', for display

comment on column grade_thresholds.combination is
  'Cambridge''s own option code for this combination of components, e.g. ''AX''.';
comment on column grade_thresholds.components is
  'The components making up this combination, comma-separated, e.g. ''11,21,31''.';

alter table grade_thresholds
  add constraint grade_thresholds_subject_session_combination_grade_key
  unique (subject_id, exam_session_id, combination, grade);

-- The other table each PDF publishes: thresholds per individual component,
-- one row per grade (A-E, no A*). A separate table rather than a nullable
-- "component" column on grade_thresholds above -- the two publish different
-- grade scales (A-E vs A*-E) for different things (one component vs a whole
-- combination), so a shared row shape would need nullable columns either
-- way used, which is exactly the ambiguity a second table avoids.
create table component_grade_thresholds (
  id              uuid primary key default gen_random_uuid(),
  subject_id      uuid not null references subjects(id) on delete cascade,
  exam_session_id uuid not null references exam_sessions(id) on delete cascade,
  component       int  not null check (component between 1 and 9),
  grade           text not null,
  min_mark        int  not null,
  max_mark        int  not null,
  unique (subject_id, exam_session_id, component, grade)
);

alter table component_grade_thresholds enable row level security;

-- Same argument 0016 already made for grade_thresholds itself: this is
-- published content, not anything gated by review or ownership.
create policy component_grade_thresholds_public on component_grade_thresholds
  for select using (true);
