-- 0014_review_queue_widened.sql
-- Everything unapproved reaches the queue, not only what the pipeline doubted.
--
-- 0012 defined the queue as extraction_status = 'needs_review', which quietly
-- contradicts the rule the whole schema is built around: nothing reaches a
-- student until a human has signed it off. A question the pipeline was happy
-- with lands as 'extracted' — not flagged, and under that definition not
-- queued either, so it would have sat unapproved and invisible forever, in a
-- state no screen in the product could reach.
--
-- The flag is what changes, not the gate: a flagged question is a question the
-- reviewer should open first, and `extraction_status` is carried out of the
-- view so the caller can order on it.

-- Dropped and recreated rather than replaced: `create or replace view` can only
-- append columns, and this one inserts extraction_status among the existing
-- ones. Nothing depends on the view but the reviewer page.
drop view if exists v_review_queue;

-- The review queue. Nothing here is visible to a student: RLS admits only
-- approved questions, and every row in this view is by definition not approved,
-- so it returns nothing at all except to the service role.
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
  q.extraction_status,
  q.review_flags,
  (
    select min(qa.page_number)
      from question_assets qa
     where qa.question_id = q.id and qa.kind = 'question_crop'
  ) as page_number,
  (
    select qa.storage_key
      from question_assets qa
     where qa.question_id = q.id and qa.kind = 'question_crop'
     order by qa.sort_order, qa.page_number
     limit 1
  ) as crop_storage_key,
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
where q.extraction_status in ('extracted', 'needs_review');

-- create or replace does not carry privileges forward or back: 0013's revoke
-- still stands, and this restates it so the file is safe to apply on its own.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_review_queue from anon, authenticated;
  end if;
end
$$;
