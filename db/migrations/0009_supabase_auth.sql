-- 0009_supabase_auth.sql
-- SUPABASE ONLY. Skip this file on plain Postgres — auth.users does not exist
-- there and the migration will fail.
--
-- Ties profiles to Supabase Auth and creates a profile + free subscription the
-- moment a user signs up, so no code path has to cope with a logged-in user who
-- has no profile row.

alter table profiles
  add constraint profiles_auth_user_fk
  foreign key (id) references auth.users(id) on delete cascade;

create or replace function handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email, '@', 1))
  )
  on conflict (id) do nothing;

  insert into public.subscriptions (user_id, plan_tier, status)
  values (new.id, 'free', 'active')
  on conflict do nothing;

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();
