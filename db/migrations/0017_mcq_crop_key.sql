-- 0017_mcq_crop_key.sql
-- Give the arena the crop it is supposed to be showing.
--
-- A multiple-choice question ingested geometrically has a number, an answer and
-- a crop box, and no text at all — reading order in these papers is scrambled,
-- so the text is deliberately not written. The crop *is* the question: the
-- diagrams, the graphs, the four circuit diagrams that a 2015 question uses as
-- its options. Without the key here the arena has a question it cannot draw.
--
-- The key, not a URL. URLs into a private bucket are signed and expire; the key
-- is stable, so the pages that use it can stay statically generated and resolve
-- it through /api/asset at the moment someone actually looks.

drop view if exists v_mcq_questions;

-- The arena's source. A multiple-choice question with no recorded answer is not
-- servable at all, so the filter is part of the definition rather than something
-- each caller has to remember.
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
  ) as topic_codes
from questions q
join papers p on p.id = q.paper_id
join subjects sub on sub.id = p.subject_id
where q.question_type = 'mcq'
  and q.correct_option is not null;
