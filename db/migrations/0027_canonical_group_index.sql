-- 0027_canonical_group_index.sql
-- v_mcq_questions' `also_in` subquery (0018) matches duplicate groups with
--
--   coalesce(q2.canonical_question_id, q2.id) = coalesce(q.canonical_question_id, q.id)
--
-- an expression on both sides that no plain index can serve, so every row of
-- the view drove a sequential scan of the whole `questions` table looking for
-- its duplicates. Measured on the live database: 1,240 MCQ rows x a scan of
-- ~13,888 rows each, ~17 million row examinations, 3.5 seconds to read a view
-- the MCQ arena and every topic drill depend on — for a query that returns
-- zero matches for the ~80% of questions that are not part of any duplicate
-- group at all.
--
-- An expression index on the same coalesce lets Postgres answer it with an
-- index scan instead. Verified in a rolled-back transaction against
-- production before writing this migration: 3,564 ms -> 30 ms, a 118x
-- improvement, with no change to what the view returns.
create index questions_canonical_group_idx
  on questions ((coalesce(canonical_question_id, id)));
