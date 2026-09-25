-- 0042_mark_scheme_crops.sql
-- Expose the picture of each mark-scheme row, where there is one.
--
-- Mathematics mark schemes are typeset maths in a table, and their text layer
-- scatters fractions and drops symbols, so `mark_scheme_text` is only a search
-- index for that subject. The row itself is stored as a `mark_scheme_crop` asset
-- of the leaf question (`noteacademy mark-scheme-crops`), and this view carries
-- it beside the text so the arena can show the picture and fall back to the
-- text when a row has none.
--
-- `parts[].markSchemeCrops` is a new key inside the existing `parts` json, and
-- `mark_scheme_crops` (the top-level question's own row images, for a question
-- with no parts) is appended as the view's last column, the only place
-- `create or replace view` allows a new one.

create or replace view v_structured_questions with (security_invoker = true) as
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
                 'pageNumber', qa.page_number, 'storageKey', qa.storage_key,
                 'aspectRatio',
                   round(((qa.bbox[3] - qa.bbox[1]) / nullif(qa.bbox[4] - qa.bbox[2], 0))::numeric, 4)
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
                 'markSchemeText', leaf.mark_scheme_text,
                 'markSchemeCrops', coalesce(
                   (
                     select jsonb_agg(
                              jsonb_build_object(
                                'pageNumber', mqa.page_number, 'storageKey', mqa.storage_key,
                                'aspectRatio',
                                  round(((mqa.bbox[3] - mqa.bbox[1])
                                         / nullif(mqa.bbox[4] - mqa.bbox[2], 0))::numeric, 4)
                              )
                              order by mqa.sort_order
                            )
                       from question_assets mqa
                      where mqa.question_id = leaf.id and mqa.kind = 'mark_scheme_crop'
                   ),
                   '[]'::jsonb
                 )
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
  ) as parts,
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object(
                 'pageNumber', mqa.page_number, 'storageKey', mqa.storage_key,
                 'aspectRatio',
                   round(((mqa.bbox[3] - mqa.bbox[1]) / nullif(mqa.bbox[4] - mqa.bbox[2], 0))::numeric, 4)
               )
               order by mqa.sort_order
             )
        from question_assets mqa
       where mqa.question_id = q.id and mqa.kind = 'mark_scheme_crop'
    ),
    '[]'::jsonb
  ) as mark_scheme_crops
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
where q.question_type = 'structured'
  and q.parent_question_id is null;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    grant select on v_structured_questions to anon, authenticated;
  end if;
end
$$;
