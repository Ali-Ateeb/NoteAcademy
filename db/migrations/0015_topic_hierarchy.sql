-- 0015_topic_hierarchy.sql
-- Carry the shape of the topic tree out to the application.
--
-- A CAIE topic tree is three deep — '4', '4.2', '4.2.4' — and 0012's view
-- flattened it, which is fine for a list and wrong for everything else. Twenty
-- of Physics 5054's eighty-three nodes are containers: they exist to group
-- their children and have no learning outcomes of their own. Rendered as a flat
-- list they are twenty links to an empty page.
--
-- `parent_code` and `depth` are enough to rebuild the tree client-side without
-- a recursive query, and `is_revisable` names the distinction that actually
-- matters: a node with outcomes is something a student can revise and a
-- question can be tagged against. A container is neither.

drop view if exists v_topics;

create or replace view v_topics with (security_invoker = true) as
select
  sub.slug as subject_slug,
  t.code,
  t.slug,
  t.title,
  t.learning_objectives,
  t.sort_order,
  -- '4.2.4' -> '4.2'; null for a section.
  nullif(regexp_replace(t.code, '\.[^.]+$', ''), t.code) as parent_code,
  (length(t.code) - length(replace(t.code, '.', ''))) as depth,
  (coalesce(array_length(t.learning_objectives, 1), 0) > 0) as is_revisable,
  (
    select count(*)
      from question_topics qt
      join questions q on q.id = qt.question_id
     where qt.topic_id = t.id
  ) as question_count
from topics t
join syllabus_versions sv on sv.id = t.syllabus_version_id and sv.is_current
join subjects sub on sub.id = sv.subject_id;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    grant select on v_topics to anon, authenticated;
  end if;
end
$$;
