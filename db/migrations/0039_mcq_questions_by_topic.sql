-- 0039_mcq_questions_by_topic.sql
-- Fetch a topic's questions without computing every question's topics first.
--
-- `getQuestionsByTopic` filtered `v_mcq_questions` on `topic_codes @> [code]`,
-- but `topic_codes` is itself an aggregate the view computes per row -- so
-- Postgres could not filter until it had built that array for every MCQ in
-- the subject (1,802 of them for physics, ~9,000 buffer reads) just to keep
-- the 67 that matched. Fine for one visitor: ~80ms. Not fine for a
-- production build, which asks this question for every topic page of every
-- subject at once (~330 pages), against an `anon` role whose statement
-- timeout is 3 seconds -- so the tail of the queue times out, and the build
-- fails on a different topic each run.
--
-- The rows are chosen first, by the indexed link table, and only then does
-- the view's per-row work (options, crop, duplicates) run -- for the handful
-- of questions that matter, not all of them. `security invoker` (the
-- default) keeps this exactly as visible as the view is: approved questions
-- only, to a student.
--
-- Same population as the old filter: any tag, primary or secondary, counts.
create or replace function mcq_questions_by_topics(
  p_subject_slug text,
  p_topic_codes  text[]
) returns setof v_mcq_questions
language sql
stable
security invoker
as $$
  select v.*
    from v_mcq_questions v
   where v.subject_slug = p_subject_slug
     and v.canonical_question_id is null
     and v.id in (
       select qt.question_id
         from question_topics qt
         join topics t on t.id = qt.topic_id
        where t.code = any (p_topic_codes)
     );
$$;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    grant execute on function mcq_questions_by_topics(text, text[]) to anon, authenticated;
  end if;
end
$$;
