-- 0032_topic_question_count.sql
-- Make v_topics.question_count count what clicking the topic actually shows.
--
-- The badge beside a topic said 76 for physics 1.8 Pressure; the topic page
-- listed 23. Three separate over-counts, all of them invisible from the number
-- itself:
--
--   * duplicates. CAIE reuses most multiple-choice items between the variants
--     of one sitting, and `getQuestionsByTopic` folds them (`canonical_question_id
--     is null`) precisely so cross-paper browsing does not spend a student's
--     attention twice on the same question. The count did not fold them: 8 of
--     the 76.
--   * structured questions. The topic page reads v_mcq_questions and shows
--     multiple choice only; 45 of the 76 were structured questions it never
--     lists. (Their marks are still counted by total_marks below, which is a
--     different claim -- "how much is this topic worth" rather than "how much
--     can I practise here" -- and stays as it is.)
--   * approval, which RLS was already handling for students but not for the
--     service role, so the same view answered differently depending on who
--     asked. Stated explicitly here instead.
--
-- Secondary tags are deliberately still counted: the page finds a question by
-- `topic_codes @> [code]`, which includes the ones where this topic is the
-- secondary, so excluding them here would swap one mismatch for another.
--
-- total_marks is left exactly as 0031 defined it. The two columns now answer
-- two different questions, which is the honest arrangement -- 0031's own note
-- about keeping them consistent was written when both were wrong in the same
-- direction.
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
       and q.question_type = 'mcq'
       and q.extraction_status = 'approved'
       and q.canonical_question_id is null
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
