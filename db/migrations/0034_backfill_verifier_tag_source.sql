-- 0034_backfill_verifier_tag_source.sql
-- Relabel the verifier's existing second-opinion rows as source = 'verifier'.
--
-- verify.py's `_apply_one` writes a disagreement at exactly confidence 0.6
-- (see verify.py:557) and nowhere else in the pipeline writes that value on
-- a non-primary row -- 0.6 is otherwise unused, so the tolerance below
-- (float4 equality misses; see the pipeline's own notes on this) picks out
-- precisely the rows 0033 was added for and none of the 167 genuine
-- editorial secondaries the original tagger wrote at 0.80-0.90.
update question_topics
   set source = 'verifier'
 where not is_primary
   and source = 'model'
   and abs(confidence - 0.6) < 0.001;
