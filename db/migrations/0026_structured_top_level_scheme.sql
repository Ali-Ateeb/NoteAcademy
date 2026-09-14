-- 0026_structured_top_level_scheme.sql
-- A structured question's own mark scheme, for when it has no parts.
--
-- Segmentation finds lettered sub-parts by looking for a label like
-- "9(a)" under "9" — when a question genuinely has none (CAIE prints some
-- structured questions as one flat ask, no (a)/(b)/(c)), `parts` in
-- v_structured_questions is an empty array, not null. Both the arena and the
-- review queue then took `question.parts` as truthy on that empty array and
-- rendered the parts branch — which has nothing to iterate — instead of
-- falling back to the question's own scheme, even though six approved
-- questions have real marks and mark scheme text sitting right there on the
-- top-level row. A student saw the crop and no way to mark it at all.
--
-- The application-side fix (StructuredArena.tsx, ReviewQueue.tsx) checks
-- parts.length rather than parts' truthiness and synthesizes a single part
-- from these two columns when there are none. This just exposes them.
--
-- Dropped first: CREATE OR REPLACE only allows appending columns at the end
-- of the existing list, and mark_scheme/max_marks are inserted ahead of the
-- existing crops/parts columns below.
drop view if exists v_structured_questions;

create view v_structured_questions with (security_invoker = true) as
select
  q.id,
  p.slug as paper_slug,
  sub.slug as subject_slug,
  q.display_label,
  q.ordinal,
  q.question_text,
  q.mark_scheme_text as mark_scheme,
  q.max_marks,
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
