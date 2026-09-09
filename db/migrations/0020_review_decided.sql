-- 0020_review_decided.sql
-- v_review_queue only ever shows 'needs_review' questions, on purpose: it is
-- the reviewer's queue of what is left to do, not a log of what was done. But
-- that means an approved question disappears from every admin surface the
-- moment it is decided — the browser's own local decision record still knows
-- about it until the next reload, which is why it looked like it vanished
-- rather than published. There was no way to find it again afterward except
-- by already knowing its paper slug and question number by heart, which is
-- exactly the thing a reviewer noticing a mistake days later does not have.
--
-- This is that lookup: every approved or rejected question, newest decision
-- first, with the topic it currently carries. A service-role surface, the
-- same as v_review_queue and for the same reason — a rejected question is
-- unapproved by definition, so row level security would refuse it to anyone
-- else anyway, and this view is not meant to be a second public listing of
-- approved content next to the ones RLS already governs.
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
where q.extraction_status in ('approved', 'rejected');

-- No grant to anon or authenticated, matching v_review_queue: this view exists
-- for the service role behind /admin/review, not for the public API surface.
