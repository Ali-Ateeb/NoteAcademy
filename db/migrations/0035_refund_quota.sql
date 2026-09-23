-- 0035_refund_quota.sql
-- Give /api/solve a way to give back a quota unit it charged for nothing.
--
-- consume_quota already rolls back its own consumption when the caller is
-- over the daily limit, but that is the only rollback it knows about: the
-- route calls it *before* the actual Gemini call, so a network failure or an
-- empty model response left the student down one of their five free daily
-- solves for an answer they never received, with no way back. This is the
-- other half of that same counter -- called only from the route's own
-- catch path, once, and only for a request that consumed successfully but
-- then failed to produce a solution.
--
-- Floored at 0 by the same check constraint `usage_counters.used` already
-- has, via greatest(): a refund can only ever return units this same day's
-- row actually holds, never manufacture credit past what was consumed.
create or replace function refund_quota(
  p_user_id uuid,
  p_metric  text,
  p_amount  int default 1
) returns void
language plpgsql
as $$
begin
  update usage_counters
     set used = greatest(used - p_amount, 0)
   where user_id = p_user_id and metric = p_metric and day = current_date;
end;
$$;

alter function refund_quota(uuid, text, int) set search_path = public, pg_temp;
