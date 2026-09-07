-- 0002_syllabus.sql
-- Syllabus taxonomy: levels -> subjects -> syllabus versions -> topic tree.
--
-- The version layer is the part most clones omit. CAIE revises syllabuses on a
-- rolling basis, so "Kinematics" in the 2020-2022 Physics 5054 syllabus is not
-- necessarily the same node as in 2023-2025, and a 2014 question may test an
-- outcome that no longer exists. Topics therefore hang off a *version*, never
-- off the subject directly, and topic_links carries questions across revisions.

create type level_code as enum ('o_level', 'igcse', 'as_level', 'a_level');

create table levels (
  id          uuid primary key default gen_random_uuid(),
  code        level_code  not null unique,
  name        text        not null,
  slug        text        not null unique,
  sort_order  int         not null default 0
);

create table subjects (
  id            uuid primary key default gen_random_uuid(),
  level_id      uuid not null references levels(id) on delete restrict,
  syllabus_code text not null,                      -- '5054', '4024', '9701'
  title         text not null,                      -- 'Physics'
  slug          text not null,                      -- 'physics-5054'
  description   text,
  is_published  boolean not null default false,     -- gates it out of the public directory
  created_at    timestamptz not null default now(),
  unique (level_id, syllabus_code),
  unique (level_id, slug)
);

create table syllabus_versions (
  id              uuid primary key default gen_random_uuid(),
  subject_id      uuid not null references subjects(id) on delete cascade,
  label           text not null,                    -- '2023-2025'
  first_exam_year int  not null,
  last_exam_year  int,                              -- null = current, no end announced
  source_url      text,                             -- the published CAIE syllabus PDF
  is_current      boolean not null default false,
  unique (subject_id, label)
);

-- Exactly one current version per subject.
create unique index syllabus_versions_one_current
  on syllabus_versions (subject_id) where is_current;

create table topics (
  id                  uuid primary key default gen_random_uuid(),
  syllabus_version_id uuid not null references syllabus_versions(id) on delete cascade,
  parent_topic_id     uuid references topics(id) on delete cascade,
  code                text not null,                -- '1.2' — CAIE's own unit numbering
  title               text not null,
  slug                text not null,
  -- Verbatim learning outcomes from the syllabus. These are fed to the tagging
  -- classifier as a closed enum, which is why they live here rather than in a
  -- prompt file: the classifier can only choose outcomes that actually exist.
  learning_objectives text[] not null default '{}',
  sort_order          int not null default 0,
  unique (syllabus_version_id, code)
);

create index topics_parent_idx on topics (parent_topic_id);
create index topics_version_idx on topics (syllabus_version_id);

-- Maps a topic in one syllabus version onto its counterpart in another, so a
-- student on the 2023 syllabus can be shown 2015 questions without being served
-- content that was withdrawn.
create type topic_relation as enum ('same', 'split_into', 'merged_into', 'withdrawn');

-- to_topic_id is null when relation = 'withdrawn' (the outcome left the
-- syllabus with no successor), so the natural key needs a surrogate primary key
-- plus partial unique indexes rather than a composite PK over nullable columns.
create table topic_links (
  id            uuid primary key default gen_random_uuid(),
  from_topic_id uuid not null references topics(id) on delete cascade,
  to_topic_id   uuid references topics(id) on delete cascade,
  relation      topic_relation not null,
  check ((relation = 'withdrawn') = (to_topic_id is null))
);

create unique index topic_links_unique
  on topic_links (from_topic_id, to_topic_id, relation)
  where to_topic_id is not null;

create unique index topic_links_withdrawn_unique
  on topic_links (from_topic_id)
  where to_topic_id is null;
