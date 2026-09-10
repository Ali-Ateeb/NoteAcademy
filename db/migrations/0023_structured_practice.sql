-- 0023_structured_practice.sql
-- Structured questions, readable by students for the first time.
--
-- Everything so far treated a structured question's *database rows* as the
-- unit: 0022 fixed this for the review queue, where 79 rows for an
-- 11-question paper meant 68 cards reading "no crop to show" for a sub-part
-- that was never meant to have one of its own. The same shape problem sits
-- one layer up, in what a student is told a paper contains and in what the
-- arena has to read to run one: the practice unit is the top-level
-- question — the crop, and everything under it — not each row.

-- v_papers counted every row a paper has, which is exactly right for an mcq
-- paper (one row is one question) and wrong for a structured one: a count of
-- 79 for an 11-question paper describes the database, not the paper. Only
-- the denominator changes here — a structured paper's rows, its RLS, and
-- every other column are untouched.
drop view if exists v_papers;

create or replace view v_papers with (security_invoker = true) as
select
  p.slug,
  sub.slug as subject_slug,
  es.year,
  es.season,
  p.component,
  p.variant,
  coalesce(
    p.question_type,
    (select mode() within group (order by q.question_type)
       from questions q where q.paper_id = p.id)
  ) as question_type,
  (
    select count(*) from questions q
     where q.paper_id = p.id
       and (q.question_type <> 'structured' or q.parent_question_id is null)
  ) as question_count,
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object('docType', pd.doc_type, 'pageCount', pd.page_count)
               order by pd.doc_type
             )
        from paper_documents pd where pd.paper_id = p.id
    ),
    '[]'::jsonb
  ) as documents
from papers p
join subjects sub on sub.id = p.subject_id
join exam_sessions es on es.id = p.exam_session_id;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_papers from anon, authenticated;
    grant select on v_papers to anon, authenticated;
  end if;
end
$$;

-- The arena's source for a structured paper, the counterpart to
-- v_mcq_questions. RLS does the actual gating, same as everywhere else this
-- pattern is used: `security_invoker = true` means every table this view
-- touches — including the leaf rows in the correlated subquery below — is
-- read as the calling role, and questions_public only admits
-- extraction_status = 'approved'. A leaf that was somehow left unapproved
-- while its top-level question was approved simply does not appear in
-- `parts`, rather than the view needing to know to check for it.
create or replace view v_structured_questions with (security_invoker = true) as
select
  q.id,
  p.slug as paper_slug,
  sub.slug as subject_slug,
  q.display_label,
  q.ordinal,
  q.question_text,
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object(
                 'pageNumber', qa.page_number, 'storageKey', qa.storage_key
               )
               order by qa.sort_order
             )
        from question_assets qa
       where qa.question_id = q.id and qa.kind = 'question_crop'
    ),
    '[]'::jsonb
  ) as crops,
  -- Leaves only, in paper order — a container like "9(a)" carries no marks
  -- and nothing for a student to self-mark, the same reasoning 0022 applies
  -- on the review side.
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object(
                 'displayLabel', leaf.display_label,
                 'maxMarks', leaf.max_marks,
                 'markSchemeText', leaf.mark_scheme_text
               )
               order by leaf.ordinal
             )
        from questions leaf
       where leaf.paper_id = q.paper_id
         and leaf.display_label like q.display_label || '(%'
         and not exists (
           select 1 from questions grandchild
            where grandchild.parent_question_id = leaf.id
         )
    ),
    '[]'::jsonb
  ) as parts
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
where q.question_type = 'structured'
  and q.parent_question_id is null;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_structured_questions from anon, authenticated;
    grant select on v_structured_questions to anon, authenticated;
  end if;
end
$$;
