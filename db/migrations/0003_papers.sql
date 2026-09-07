-- 0003_papers.sql
-- Exam sessions, papers, and the documents that make up a paper.
--
-- The important split: a *paper* is the logical exam sitting (Physics 5054,
-- May/June 2019, component 1, variant 2). Its question paper, mark scheme,
-- examiner report and grade thresholds are *documents attached to it*. Modelling
-- doc_type as a column on `papers` — as most schemas do — makes the QP and its
-- mark scheme unrelated rows, and the split-screen viewer then has nothing to
-- join on.

create type exam_season as enum ('feb_march', 'may_june', 'oct_nov');
create type paper_doc_type as enum (
  'qp',  -- question paper
  'ms',  -- mark scheme
  'er',  -- examiner report        (usually one per component, all variants)
  'gt',  -- grade thresholds       (one per session, all components)
  'in',  -- insert / data booklet
  'ci'   -- confidential instructions (practical papers)
);

create table exam_sessions (
  id     uuid primary key default gen_random_uuid(),
  year   int not null check (year between 1990 and 2100),
  season exam_season not null,
  slug   text not null unique,     -- '2019-may-june'
  unique (year, season)
);

create table papers (
  id              uuid primary key default gen_random_uuid(),
  subject_id      uuid not null references subjects(id) on delete cascade,
  exam_session_id uuid not null references exam_sessions(id) on delete restrict,
  component       int  not null check (component between 1 and 9),
  variant         int  check (variant between 0 and 9),  -- null: paper has no variants
  slug            text not null unique,   -- 'physics-5054-2019-may-june-p12'
  created_at      timestamptz not null default now(),
  unique (subject_id, exam_session_id, component, variant)
);

create index papers_subject_idx on papers (subject_id);
create index papers_session_idx on papers (exam_session_id);

create table paper_documents (
  id          uuid primary key default gen_random_uuid(),
  paper_id    uuid not null references papers(id) on delete cascade,
  doc_type    paper_doc_type not null,
  storage_key text not null,               -- object-storage key; never a public URL
  page_count  int,
  byte_size   bigint,
  -- sha256 of the source file. Deduplicates re-downloads and detects the case
  -- where a document is silently replaced upstream after we have already
  -- extracted questions from it.
  checksum    text,
  source_url  text,
  ingested_at timestamptz not null default now(),
  unique (paper_id, doc_type)
);

create index paper_documents_paper_idx on paper_documents (paper_id);

-- Grade thresholds are published per component for a whole session, so they are
-- their own small table rather than a column on papers.
create table grade_thresholds (
  id              uuid primary key default gen_random_uuid(),
  subject_id      uuid not null references subjects(id) on delete cascade,
  exam_session_id uuid not null references exam_sessions(id) on delete cascade,
  grade           text not null,          -- 'A*', 'A', 'B', ...
  min_mark        int  not null,
  max_mark        int  not null,
  unique (subject_id, exam_session_id, grade)
);
