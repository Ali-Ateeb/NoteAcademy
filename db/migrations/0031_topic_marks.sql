-- 0031_topic_marks.sql
-- How many marks a topic has actually been worth, historically — not just
-- how many questions test it.
--
-- v_topics' question_count (0015) already answers "how much of this topic
-- have I seen"; it cannot answer "is this topic worth revising before that
-- one", because two topics with five questions each are not equally
-- important if one is all 1-mark MCQs and the other is all 6-mark
-- structured parts. This is the CAIE-specific signal a generic revision app
-- has no way to compute — it needs the mark scheme's own numbers, not a
-- question count — and it's exactly what the marks-weighted revision
-- priority feature is built on.
--
-- An MCQ is a flat 1 mark, the same convention the practice arena's own
-- scoring already assumes. A structured top-level question's marks live on
-- its leaves when it has any; StructuredArena.tsx's own `effectiveParts`
-- fallback uses the identical precedence for the identical reason (0026):
-- leaves first, the top-level's own `max_marks` only when it has none.
--
-- Deliberately not filtered to extraction_status = 'approved': question_count
-- above isn't either, and filtering only the new column would make the two
-- disagree about which questions "count" for the same topic.
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
  ) as question_count,
  (
    select coalesce(sum(
      case
        when q.question_type = 'mcq' then 1
        else coalesce(
          (
            select sum(leaf.max_marks)
              from questions leaf
             where leaf.paper_id = q.paper_id
               and leaf.display_label like q.display_label || '(%'
               and not exists (
                 select 1 from questions grandchild
                  where grandchild.parent_question_id = leaf.id
               )
          ),
          q.max_marks,
          0
        )
      end
    ), 0)
      from question_topics qt
      join questions q on q.id = qt.question_id
     where qt.topic_id = t.id
  ) as total_marks
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
