-- 0010_question_options.sql
-- What each multiple-choice option actually says.
--
-- 0004 records *which* option is correct but not what any of them read, so a
-- question could be marked and never displayed. The arena needs both, and the
-- text is also what the search index and the tagging classifier see: "which
-- pair contains one scalar and one vector" is unclassifiable without its
-- options.
--
-- A row per option rather than a jsonb blob on `questions`, because options are
-- queried on their own — a distractor-analysis view ("which wrong answer did
-- students pick") joins attempts to this table, and the enum makes an invalid
-- option letter impossible rather than merely unlikely.
--
-- Note what is *not* here: the option's artwork. A 2015 circuit question has
-- four circuit diagrams as its options, and no amount of text captures them.
-- Those are question_assets crops; this table is the search and marking index,
-- the same division of labour as question_text.

create table question_options (
  question_id uuid       not null references questions(id) on delete cascade,
  option      mcq_option not null,
  content     text       not null,
  primary key (question_id, option)
);

alter table question_options enable row level security;

-- Tighter than question_assets and question_topics, which are public outright:
-- an option's text is question content, so it follows the question's own gate.
-- An unapproved question leaks nothing, not even its distractors.
create policy question_options_public on question_options
  for select using (
    exists (
      select 1 from questions q
      where q.id = question_options.question_id
        and q.extraction_status = 'approved'
    )
  );
