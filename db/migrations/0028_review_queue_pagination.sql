-- 0028_review_queue_pagination.sql
-- Push the review queue's sort and filter into SQL, so paging it is a
-- LIMIT/OFFSET instead of "fetch up to 5000 rows and sort in JavaScript".
--
-- getReviewQueue() page PostgREST in 1000-row chunks up to a hard 5000-row
-- ceiling, because the flagged-first / worst-topic-confidence sort it needs
-- lives entirely in application code -- there was no column to ORDER BY.
-- Past that ceiling it silently truncates: a queue of 5,001 shows 5,000,
-- with nothing on screen to say the last one is missing. Ten subjects at
-- this project's own current per-subject rate puts the *unfiltered* queue
-- there long before any one subject's own backlog does.
--
-- Two columns fix the sort; `subject_slug` fixes the other missing piece,
-- filtering, so a reviewer can scope a session to one subject's queue
-- (which stays a few hundred rows even at ten subjects) instead of always
-- paging through everything at once.
--
-- Appended to the existing view rather than duplicating its body: wrapping
-- 0022's select as a subquery and adding columns outside it means this file
-- has nothing to say about crops, parts or review_flags, which have not
-- changed and are not this migration's business to restate.
drop view if exists v_review_queue;

create or replace view v_review_queue with (security_invoker = true) as
select
  *,
  -- Same fact the client's own tagConfidence() computed from proposed_topics
  -- (sorted highest-confidence-first when it was built) -- moved into SQL so
  -- ORDER BY can use it directly instead of every row being fetched first.
  (extraction_status = 'needs_review') as is_flagged,
  (proposed_topics -> 0 ->> 'confidence')::real as primary_topic_confidence
from (
  select
    q.id,
    sub.slug as subject_slug,
    p.slug as paper_slug,
    sub.title || ' ' || es.year || ' Paper ' || p.component ||
      coalesce(p.variant::text, '') as paper_title,
    q.display_label,
    q.question_type,
    q.question_text,
    q.mark_scheme_text,
    q.correct_option,
    q.extraction_confidence,
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
    and (q.question_type <> 'structured' or q.parent_question_id is null)
) queue;

-- No index backs the ORDER BY: `primary_topic_confidence` depends on
-- question_topics, not a plain column of `questions`, so nothing short of a
-- materialized column or a materialized view could let Postgres skip
-- computing the queue's per-row aggregates before sorting. Not worth it yet
-- — a full sort of a few thousand small, pre-aggregated rows is milliseconds,
-- and it is the same rows this view already had to visit under the old
-- fetch-everything approach, just sorted once in Postgres instead of copied
-- to the application first. Revisit if a real `explain analyze` says otherwise.

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_review_queue from anon, authenticated;
  end if;
end
$$;
