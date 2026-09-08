-- 0011_paper_type_and_flags.sql
-- Two facts the application asks for and the schema could not answer.

-- 1. What kind of paper this is.
--
-- It was previously only derivable by looking at the questions already loaded,
-- which inverts the dependency: the practice arena has to know a paper is
-- multiple-choice *before* deciding whether it can be sat, and an un-ingested
-- component 1 would have read as 'structured' simply because nothing had been
-- loaded from it yet. It is a property of the sitting, known from the syllabus
-- at the moment the paper row is created.
--
-- Nullable: a paper can exist before anyone has said what it contains.
alter table papers add column question_type question_type;

-- 2. Why the pipeline refused to publish a question.
--
-- The reviewer queue was already ordered by confidence, but a bare confidence
-- score does not tell a reviewer where to look. These say which specific check
-- fired — the cross-check against the page's text layer, the bbox sanity check,
-- the mark-scheme matcher, the tagging floor — and that is what makes a queue of
-- thousands tractable: the reviewer knows what they are verifying before they
-- open the crop.
--
-- An enum, not free text: every value here has a matching explanation in the
-- reviewer UI (web/src/lib/data/types.ts), and a flag with no explanation is a
-- flag a reviewer learns to ignore.
create type review_flag as enum (
  'low_tag_confidence',    -- classifier below the confidence floor
  'text_layer_mismatch',   -- the reported label is absent from the page's own text
  'bbox_outside_page',     -- the crop ran past the page edge and was clamped
  'unmatched_mark_scheme', -- no mark scheme entry claimed this label
  'ambiguous_mark_scheme', -- two entries claimed it, so both were withheld
  'no_text_layer'          -- scanned page: nothing to cross-check against
);

alter table questions add column review_flags review_flag[] not null default '{}';

-- Anything flagged is, by definition, not yet published, so the index only
-- needs to cover the queue itself.
create index questions_flagged_idx on questions using gin (review_flags)
  where extraction_status = 'needs_review';
