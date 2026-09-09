-- 0018_question_duplicates.sql
-- CAIE variants of one exam sitting reuse most multiple-choice items verbatim
-- — component 11 and component 12 of the same session share nearly all of
-- their MCQs, sometimes with the options reordered. `verify.py` already had
-- to solve this (a reviewer should look at a repeated question once, not once
-- per variant it happens to appear in), but only in memory, for the length of
-- one tagging pass. This makes it a fact about the question, so the topical
-- browser and drills can fold the same duplicates without re-deriving them,
-- and so a student can be told a question is one CAIE actually reuses.
--
-- The timed arena is deliberately untouched: it lists a paper's own questions
-- straight off `questions`, and a paper's own count and contents have to stay
-- exactly what was printed regardless of what else shares its wording.

alter table questions
  add column canonical_question_id uuid references questions(id),
  add constraint questions_canonical_not_self
    check (canonical_question_id is null or canonical_question_id <> id);

create index questions_canonical_idx on questions (canonical_question_id)
  where canonical_question_id is not null;

comment on column questions.canonical_question_id is
  'Set when this question is a verbatim duplicate of another paper''s question '
  '(the same MCQ shared across CAIE variants of one sitting). Null for a '
  'question that is unique, or that is itself the one duplicates point to. '
  'Populated by the pipeline''s `dedupe` command; never set by the review '
  'queue, and never touched by the review decision itself.';

-- v_mcq_questions, extended with the duplicate group rather than filtered by
-- it: the arena reads this view per-paper and must keep showing every
-- question a paper actually contains, so folding happens in the caller
-- (getQuestionsByTopic), not here. `also_in` lists every other sitting a
-- question's own duplicate group appears in, so either the canonical row or
-- one of its duplicates can say "this also appears in ...".
drop view if exists v_mcq_questions;

create or replace view v_mcq_questions with (security_invoker = true) as
select
  q.id,
  p.slug as paper_slug,
  sub.slug as subject_slug,
  q.display_label,
  q.ordinal,
  q.question_text,
  q.correct_option,
  q.mark_scheme_text,
  q.examiner_comment,
  q.canonical_question_id,
  (
    select qa.storage_key
      from question_assets qa
     where qa.question_id = q.id and qa.kind = 'question_crop'
     order by qa.sort_order, qa.page_number
     limit 1
  ) as crop_storage_key,
  coalesce(
    (select jsonb_object_agg(o.option::text, o.content)
       from question_options o where o.question_id = q.id),
    '{}'::jsonb
  ) as options,
  coalesce(
    (
      select array_agg(t.code order by qt.is_primary desc, t.code)
        from question_topics qt
        join topics t on t.id = qt.topic_id
       where qt.question_id = q.id
    ),
    '{}'::text[]
  ) as topic_codes,
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object(
                 'paperSlug', p2.slug, 'displayLabel', q2.display_label,
                 'year', es2.year, 'season', es2.season,
                 'component', p2.component, 'variant', p2.variant
               )
               order by es2.year desc, p2.slug
             )
        from questions q2
        join papers p2 on p2.id = q2.paper_id
        join exam_sessions es2 on es2.id = p2.exam_session_id
       where coalesce(q2.canonical_question_id, q2.id)
           = coalesce(q.canonical_question_id, q.id)
         and q2.id <> q.id
    ),
    '[]'::jsonb
  ) as also_in
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
where q.question_type = 'mcq'
  and q.correct_option is not null;

-- 0017 recreated this view with `create or replace` after a `drop view`, which
-- throws away any grant the dropped object held and leaves Supabase's default
-- (every new relation in `public` granted in full to the API roles) standing
-- in its place — anon and authenticated have carried INSERT/UPDATE/DELETE on
-- this view since, harmless only because a view built from subqueries is not
-- automatically updatable. Said out loud and made explicit here, the same
-- argument 0016 already made about a matview: protection should not rest on a
-- second mechanism continuing to agree.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_mcq_questions from anon, authenticated;
    grant select on v_mcq_questions to anon, authenticated;
  end if;
end
$$;
