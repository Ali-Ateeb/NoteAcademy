-- 0040_also_in_uses_plain_indexes.sql
-- Stop `also_in` scanning the whole questions table once per row, for anon.
--
-- 0027 gave `also_in`'s match
--
--   coalesce(q2.canonical_question_id, q2.id) = coalesce(q.canonical_question_id, q.id)
--
-- an expression index, and for the `postgres` role that worked: 3.5s -> 30ms.
-- It does not work for `anon`, the role every student page and every
-- production build actually reads as. Row level security wraps `questions`
-- in a policy filter, and the planner will not push a user-written
-- expression comparison beneath one, so it abandons the index and falls back
-- to a sequential scan of all 16,848 questions for each row the view
-- returns. Measured as anon on the largest topic (chemistry 3.3, 98
-- questions): 480ms of a 501ms query was that one sequential scan, run 98
-- times. It is invisible from any role that bypasses RLS, which is why every
-- earlier measurement in this database said the view was fast.
--
-- The same match, with the outer row's columns passed through bare -- no
-- expression wrapped around them -- and each comparison a plain column
-- equality that `questions_pkey` or `questions_canonical_idx` can serve. A
-- question's group-mates are: the rows that point at it (it is a canonical),
-- the canonical it points at (it is a duplicate), and the other rows that
-- point at that same canonical. (Trialled first with the coalesce still on
-- the outer side: same seq scan. It is that expression over a row that is
-- itself under RLS the planner refuses to use, not the inner comparison.)
-- Compared with the coalesce form for all 4,199 MCQs, both ways, before this
-- was written: 0 differ. Chemistry 3.3 as anon: ~400ms -> ~10ms.
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
       where q2.id <> q.id
         and (
               q2.canonical_question_id = q.id
            or q2.id = q.canonical_question_id
            or q2.canonical_question_id = q.canonical_question_id
         )
    ),
    '[]'::jsonb
  ) as also_in,
  (
    select round(((qa.bbox[3] - qa.bbox[1]) / nullif(qa.bbox[4] - qa.bbox[2], 0))::numeric, 4)
      from question_assets qa
     where qa.question_id = q.id and qa.kind = 'question_crop'
     order by qa.sort_order, qa.page_number
     limit 1
  ) as crop_aspect_ratio
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
where q.question_type = 'mcq'
  and q.correct_option is not null;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    grant select on v_mcq_questions to anon, authenticated;
  end if;
end
$$;
