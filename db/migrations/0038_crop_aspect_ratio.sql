-- 0038_crop_aspect_ratio.sql
-- Reserve a crop's on-page space before its bytes ever arrive.
--
-- Every crop image renders at `w-full` with its height left to the browser
-- to discover once the file loads -- so the page grows underneath whatever
-- was already below it, on every visit, for every crop. `question_assets.bbox`
-- already knows the answer (it is PDF points, not pixels, but the ratio
-- between its two dimensions is the image's own ratio regardless of the DPI
-- it was rendered at) and has since the crop was first extracted; nothing
-- needed re-deriving, only exposing.
--
-- `width_px`/`height_px` on the same table were the other candidate and are
-- not used here -- they are unpopulated on all 7,301 existing crop rows, so
-- bbox is the only source that is actually there for every crop today.
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
  ) as also_in,
  -- Appended last: `create or replace view` can only add a column at the
  -- end, never insert one, without dropping the view and losing its grants.
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
    grant select on v_mcq_questions, v_structured_questions to anon, authenticated;
  end if;
end
$$;
