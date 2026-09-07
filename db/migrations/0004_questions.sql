-- 0004_questions.sql
-- The question bank. This table is the product; everything else is plumbing.

create type question_type as enum ('mcq', 'structured', 'essay', 'practical');
create type mcq_option    as enum ('A', 'B', 'C', 'D', 'E');

-- Nothing is served to students until a human has signed it off. The pipeline
-- writes 'extracted'; the review queue moves rows to 'approved' or 'rejected'.
create type extraction_status as enum ('extracted', 'needs_review', 'approved', 'rejected');

create table questions (
  id                 uuid primary key default gen_random_uuid(),
  paper_id           uuid not null references papers(id) on delete cascade,
  -- Self-reference, so 1(a)(ii) hangs off 1(a) hangs off 1. Marks roll up the
  -- tree, and the topical engine can serve a whole part or a single sub-part.
  parent_question_id uuid references questions(id) on delete cascade,
  label              text not null,          -- '1', 'a', 'ii'  (this node only)
  display_label      text not null,          -- '1(a)(ii)'      (full path, for UI)
  ordinal            int  not null,          -- ordering among siblings
  question_type      question_type not null,
  max_marks          int check (max_marks >= 0),

  -- Text is what we search and embed. The rendered crop (question_assets) is
  -- what we show: diagrams, graphs, circuits and structural formulae do not
  -- survive text extraction, and a student needs to see the paper as printed.
  question_text      text,
  mark_scheme_text   text,
  examiner_comment   text,

  correct_option     mcq_option,             -- MCQ only

  extraction_status  extraction_status not null default 'extracted',
  extraction_confidence real check (extraction_confidence between 0 and 1),
  reviewed_by        uuid,
  reviewed_at        timestamptz,
  created_at         timestamptz not null default now(),

  unique (paper_id, display_label),
  -- An MCQ is only usable if we know the answer; a non-MCQ must not carry one.
  check ((question_type = 'mcq') or (correct_option is null))
);

create index questions_paper_idx  on questions (paper_id);
create index questions_parent_idx on questions (parent_question_id);
create index questions_status_idx on questions (extraction_status)
  where extraction_status <> 'approved';
create index questions_text_trgm  on questions using gin (question_text gin_trgm_ops);

-- Only approved MCQs with a recorded answer can enter the practice arena.
create index questions_mcq_ready_idx on questions (paper_id, ordinal)
  where question_type = 'mcq'
    and extraction_status = 'approved'
    and correct_option is not null;

create type asset_kind as enum ('question_crop', 'mark_scheme_crop', 'examiner_crop', 'figure');

create table question_assets (
  id          uuid primary key default gen_random_uuid(),
  question_id uuid not null references questions(id) on delete cascade,
  kind        asset_kind not null,
  storage_key text not null,
  page_number int not null,
  -- Crop box in PDF points on the source page, [x0, y0, x1, y1]. Kept so a crop
  -- can be re-rendered at any DPI without re-running layout analysis.
  bbox        real[4] not null,
  width_px    int,
  height_px   int,
  sort_order  int not null default 0
);

create index question_assets_question_idx on question_assets (question_id, kind);

create type tag_source as enum ('model', 'human', 'imported');

-- Many-to-many on purpose: real questions straddle topics (a mechanics question
-- that also tests graph interpretation). confidence + source is what drives the
-- human review queue — anything the classifier is unsure about gets looked at.
create table question_topics (
  question_id uuid not null references questions(id) on delete cascade,
  topic_id    uuid not null references topics(id) on delete cascade,
  confidence  real not null check (confidence between 0 and 1),
  source      tag_source not null default 'model',
  is_primary  boolean not null default false,
  created_at  timestamptz not null default now(),
  primary key (question_id, topic_id)
);

create index question_topics_topic_idx on question_topics (topic_id);
create unique index question_topics_one_primary
  on question_topics (question_id) where is_primary;
