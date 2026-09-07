-- 0006_billing.sql
-- Subscriptions, quotas, and manual payment verification.
--
-- Manual verification exists because the core market (Pakistan, India,
-- Bangladesh, the Gulf) is largely outside Stripe's reach and card ownership
-- among 16-year-olds is close to zero. A bank/wallet transfer plus an admin
-- approval ships in a day and proves willingness to pay before anyone spends
-- weeks on a gateway integration. `payment_submissions` is that queue; when a
-- real gateway lands it becomes just another provider writing the same rows.

create type plan_tier as enum ('free', 'premium', 'lifetime');
create type subscription_status as enum ('active', 'past_due', 'cancelled', 'expired');

create table subscriptions (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles(id) on delete cascade,
  plan_tier    plan_tier not null default 'free',
  status       subscription_status not null default 'active',
  started_at   timestamptz not null default now(),
  expires_at   timestamptz,
  provider     text,                      -- 'manual' | 'stripe' | 'jazzcash' | ...
  provider_ref text,
  created_at   timestamptz not null default now()
);

-- One active subscription per user; history is retained as non-active rows.
create unique index subscriptions_one_active
  on subscriptions (user_id) where status = 'active';

create type payment_status as enum ('pending', 'approved', 'rejected');

create table payment_submissions (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references profiles(id) on delete cascade,
  plan_tier      plan_tier not null,
  amount_minor   int  not null check (amount_minor > 0),  -- in the smallest unit
  currency       text not null default 'PKR',
  method         text not null,           -- 'easypaisa' | 'jazzcash' | 'bank_transfer'
  reference      text,                    -- transaction id the student typed in
  proof_storage_key text,                 -- uploaded receipt screenshot
  status         payment_status not null default 'pending',
  reviewed_by    uuid references profiles(id),
  reviewed_at    timestamptz,
  review_note    text,
  created_at     timestamptz not null default now()
);

create index payment_submissions_queue_idx on payment_submissions (created_at)
  where status = 'pending';

-- Metered free-tier limits (AI solver queries, PDF compilations). One row per
-- user per day per metric; the counter is bumped with an upsert, so a burst of
-- concurrent requests cannot over-spend the quota.
create table usage_counters (
  user_id    uuid not null references profiles(id) on delete cascade,
  metric     text not null,               -- 'ai_query' | 'pdf_export'
  day        date not null,
  used       int  not null default 0 check (used >= 0),
  primary key (user_id, metric, day)
);

create or replace function consume_quota(
  p_user_id uuid,
  p_metric  text,
  p_limit   int,
  p_amount  int default 1
) returns boolean
language plpgsql
as $$
declare
  v_used int;
begin
  insert into usage_counters (user_id, metric, day, used)
  values (p_user_id, p_metric, current_date, p_amount)
  on conflict (user_id, metric, day)
    do update set used = usage_counters.used + p_amount
  returning used into v_used;

  if v_used > p_limit then
    -- Roll back just this consumption, leaving the day's counter intact.
    update usage_counters
       set used = used - p_amount
     where user_id = p_user_id and metric = p_metric and day = current_date;
    return false;
  end if;

  return true;
end;
$$;
