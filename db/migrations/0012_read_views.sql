-- 0012_read_views.sql
-- The shapes the site reads. One view per page-level question.
--
-- Why views rather than joins assembled in the web app: the client talks to
-- PostgREST, whose embedded-resource aggregates count *rows the client can see
-- in that table*, not rows that survive the join — so a topic's question count
-- assembled client-side quietly includes questions still awaiting review. Doing
-- the join in SQL keeps one definition of "a question that counts", and the
-- next reader of catalog.ts sees a select rather than a query builder.
--
-- Every view is security_invoker, so row level security still applies through
-- them. A view is otherwise checked with its *owner's* rights, which would turn
-- each of these into a hole straight past 0008: unapproved questions, served to
-- anyone, through the front door.

create or replace view v_subjects with (security_invoker = true) as
select
  s.slug,
  l.code as level_code,
  s.syllabus_code,
  s.title,
  coalesce(s.description, '') as description,
  s.is_published,
  l.sort_order as level_sort_order
from subjects s
join levels l on l.id = s.level_id;

create or replace view v_topics with (security_invoker = true) as
select
  sub.slug as subject_slug,
  t.code,
  t.slug,
  t.title,
  t.learning_objectives,
  t.sort_order,
  (
    select count(*)
      from question_topics qt
      join questions q on q.id = qt.question_id
     where qt.topic_id = t.id
  ) as question_count
from topics t
join syllabus_versions sv on sv.id = t.syllabus_version_id and sv.is_current
join subjects sub on sub.id = sv.subject_id;

create or replace view v_papers with (security_invoker = true) as
select
  p.slug,
  sub.slug as subject_slug,
  es.year,
  es.season,
  p.component,
  p.variant,
  -- The column is authoritative; the modal type of the questions loaded so far
  -- is the fallback for papers created before 0011.
  coalesce(
    p.question_type,
    (select mode() within group (order by q.question_type)
       from questions q where q.paper_id = p.id)
  ) as question_type,
  (select count(*) from questions q where q.paper_id = p.id) as question_count,
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
where q.extraction_status = 'needs_review';

-- Supabase's API roles. Guarded so the same file applies to a plain Postgres
-- database, where these roles do not exist.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    grant select on v_subjects, v_topics, v_papers, v_mcq_questions
      to anon, authenticated;
    -- v_review_queue is deliberately excluded: it is a service-role surface.
  end if;
end
$$;
