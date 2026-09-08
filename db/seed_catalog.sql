-- seed_catalog.sql — the reference rows the pipeline and the site both need.
--
-- Not fixtures: these are the levels, subjects and syllabus versions that
-- actually exist. The pipeline resolves a subject *slug* before it can load a
-- paper (pipeline/load.py:resolve_paper_id), so a database without these rows
-- rejects every ingestion run.
--
-- Idempotent. Re-running it updates in place; it never duplicates and never
-- resets is_published, because publishing a subject is a decision made once the
-- ingestion is actually done, not something a seed file gets to revoke.
--
-- Questions, papers and exam sessions are deliberately absent. Those come from
-- ingestion, and inventing them here is the one thing this project cannot do.

insert into levels (code, name, slug, sort_order) values
  ('o_level',  'O Level',  'o-level',  1),
  ('igcse',    'IGCSE',    'igcse',    2),
  ('as_level', 'AS Level', 'as-level', 3),
  ('a_level',  'A Level',  'a-level',  4)
on conflict (code) do update set
  name = excluded.name, slug = excluded.slug, sort_order = excluded.sort_order;

-- Physics 5054 first: O Level Maths (4024) and IGCSE Maths (0580) have no
-- multiple-choice component, and multiple choice is the part that ingests
-- deterministically — geometric segmentation, an answer key read from the mark
-- scheme's text layer, no model in the loop. Chemistry and Biology follow
-- because they have MCQ Paper 1s too.
insert into subjects (level_id, syllabus_code, title, slug, description, is_published)
select l.id, v.syllabus_code, v.title, v.slug, v.description, v.is_published
from levels l
join (values
  ('o_level'::level_code, '5054', 'Physics', 'physics-5054',
   'Past papers split into individual questions, each carrying its own mark scheme. Multiple-choice papers can be sat as timed practice.',
   false),
  ('o_level', '5070', 'Chemistry', 'chemistry-5070',
   'Ingestion in progress.', false),
  ('o_level', '5090', 'Biology', 'biology-5090',
   'Ingestion in progress.', false),
  ('o_level', '4024', 'Mathematics (Syllabus D)', 'mathematics-4024',
   'Ingestion in progress.', false)
) as v(level_code, syllabus_code, title, slug, description, is_published)
  on v.level_code = l.code
on conflict (level_id, syllabus_code) do update set
  title = excluded.title,
  slug  = excluded.slug,
  description = excluded.description;
  -- is_published intentionally not touched: see the header.

-- The version a question is tagged *against*. Topics hang off this, never off
-- the subject, so a 2015 question can be shown to a student sitting the current
-- syllabus without serving them an outcome that has since been withdrawn.
--
-- The topic tree itself is not seeded here: it comes from the published
-- syllabus PDF, through `noteacademy load-syllabus`. A tree that is nearly
-- right is worse than none at all — it silently mistags the bank and sends
-- students to revise the wrong unit — so it is parsed from the source document
-- rather than transcribed, and the version row below is what it hangs off.
insert into syllabus_versions (subject_id, label, first_exam_year, last_exam_year, is_current)
select s.id, '2023-2025', 2023, 2025, true
from subjects s where s.slug = 'physics-5054'
on conflict (subject_id, label) do update set
  first_exam_year = excluded.first_exam_year,
  last_exam_year  = excluded.last_exam_year;
