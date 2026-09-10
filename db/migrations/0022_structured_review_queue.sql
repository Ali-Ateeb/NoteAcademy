-- 0022_structured_review_queue.sql
-- The review queue, extended to structured (Paper 2 style) questions.
--
-- A structured question is a tree: "9" has parts "(a)"/"(b)"/"(c)", each with
-- its own sub-parts, and only the leaves carry marks or a mark scheme entry.
-- But the crop a reviewer actually looks at is attached once, to the
-- top-level question — CAIE routinely shares one figure across every part
-- that needs it, so slicing a crop per sub-part would separate a sub-part
-- from a figure it depends on (see segment_structured.py's
-- `top_level_regions`). One row per database question, this view's previous
-- shape, put 79 cards in the queue for an 11-question paper: 11 with a crop
-- to look at and 68 reading "no crop to show" for a question that was never
-- meant to have one of its own.
--
-- The fix is the same idea `top_level_regions` already uses on the pipeline
-- side: the reviewable unit is the top-level question, not the row. This
-- view now surfaces one row per top-level structured question, folding its
-- leaves' marks, mark scheme text and review flags into it, and every crop
-- page it spans (previously just the first) into an ordered array.
drop view if exists v_review_queue;

create or replace view v_review_queue with (security_invoker = true) as
select
  q.id,
  p.slug as paper_slug,
  sub.title || ' ' || es.year || ' Paper ' || p.component ||
    coalesce(p.variant::text, '') as paper_title,
  q.display_label,
  q.question_type,
  q.question_text,
  q.mark_scheme_text,
  q.correct_option,
  q.extraction_confidence,
  -- A structured container's own status never changes on its own — nothing
  -- is ever extracted or flagged against "9" itself, only its leaves — so a
  -- leaf sitting in needs_review would otherwise be invisible to the sort
  -- that puts flagged questions first, hidden under a container that reads
  -- 'extracted'. Folded up here so the queue still opens on it first.
  case
    when q.question_type = 'structured' and exists (
      select 1 from questions leaf
       where leaf.paper_id = q.paper_id
         and leaf.display_label like q.display_label || '(%'
         and leaf.extraction_status = 'needs_review'
    ) then 'needs_review'::extraction_status
    else q.extraction_status
  end as extraction_status,
  case
    when q.question_type = 'structured' then coalesce(
      (
        select array_agg(distinct flag)
          from questions leaf
          cross join lateral unnest(leaf.review_flags) as flag
         where leaf.paper_id = q.paper_id
           and leaf.display_label like q.display_label || '(%'
      ),
      '{}'::review_flag[]
    )
    else q.review_flags
  end as review_flags,
  -- Every page this question's crop spans, not only the first — a structured
  -- question can run to three or four pages, and showing only the first
  -- silently hid the rest of what the reviewer is being asked to sign off.
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object(
                 'storageKey', qa.storage_key, 'pageNumber', qa.page_number
               )
               order by qa.sort_order
             )
        from question_assets qa
       where qa.question_id = q.id and qa.kind = 'question_crop'
    ),
    '[]'::jsonb
  ) as crops,
  -- The leaves underneath a structured question: what actually carries marks
  -- and a mark scheme entry, and so what a reviewer checks the crop against.
  -- Not every descendant — an intermediate container like "9(a)" carries
  -- nothing of its own and would only pad the list with an empty entry.
  case
    when q.question_type = 'structured' then coalesce(
      (
        select jsonb_agg(
                 jsonb_build_object(
                   'displayLabel', leaf.display_label,
                   'maxMarks', leaf.max_marks,
                   'markSchemeText', leaf.mark_scheme_text,
                   'reviewFlags', leaf.review_flags
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
    )
  end as parts,
  coalesce(
    (
      select jsonb_agg(
               jsonb_build_object('code', t.code, 'title', t.title,
                                  'confidence', qt.confidence)
               order by qt.confidence desc
             )
        from question_topics qt
        join topics t on t.id = qt.topic_id
       where qt.question_id = q.id
    ),
    '[]'::jsonb
  ) as proposed_topics
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
join exam_sessions es on es.id = p.exam_session_id
where q.extraction_status in ('extracted', 'needs_review')
  -- A structured question's sub-parts are rows in their own right (for
  -- parent_question_id and for mark-scheme matching) but not queue entries
  -- of their own — see above. Everything else (mcq today) has no children,
  -- so this admits it unchanged.
  and (q.question_type <> 'structured' or q.parent_question_id is null);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_review_queue from anon, authenticated;
  end if;
end
$$;

-- v_review_decided has the same shape problem once a structured question is
-- approved or rejected: the cascade below moves every leaf to the same
-- status as its top-level question, and without this filter each one would
-- show up as its own row in the "already decided" lookup — 79 entries for an
-- 11-question paper, none but the top-level ones addressable by the retag
-- panel, which only ever looks a question up by its own display label.
create or replace view v_review_decided with (security_invoker = true) as
select
  q.id,
  p.slug as paper_slug,
  sub.title || ' ' || es.year || ' Paper ' || p.component ||
    coalesce(p.variant::text, '') as paper_title,
  q.display_label,
  q.extraction_status,
  q.reviewed_at,
  (
    select jsonb_build_object(
             'code', t.code, 'title', t.title,
             'confidence', qt.confidence, 'source', qt.source
           )
      from question_topics qt
      join topics t on t.id = qt.topic_id
     where qt.question_id = q.id and qt.is_primary
  ) as primary_topic
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
join exam_sessions es on es.id = p.exam_session_id
where q.extraction_status in ('approved', 'rejected')
  and (q.question_type <> 'structured' or q.parent_question_id is null);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_review_decided from anon, authenticated;
  end if;
end
$$;
