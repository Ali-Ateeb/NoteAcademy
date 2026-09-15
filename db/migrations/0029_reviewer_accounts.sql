-- 0029_reviewer_accounts.sql
-- Real reviewer identity, replacing the shared REVIEW_TOKEN.
--
-- Every approve/reject/undo has been indistinguishable from every other one
-- since the review queue existed: REVIEW_TOKEN is one secret, known to
-- whoever has it, and `questions.reviewed_by` has sat unpopulated since
-- 0004 because there was never an identity behind a decision to record.
-- On a queue of thousands that is not a detail -- it is the difference
-- between "a wrong topic tag was found" and "we know which reviewer's
-- judgement to look at again."
--
-- profiles.is_reviewer is the flag. Nothing reachable by a signed-in
-- student can grant it to themselves: the trigger below refuses any change
-- to the column made under the `anon` or `authenticated` Postgres roles --
-- the two roles an ordinary API request runs as -- so becoming a reviewer
-- takes the operator running SQL directly, the same way
-- `subjects.is_published` is already turned on by hand. `add column if not
-- exists` because this migration was first applied with a version of the
-- trigger that (wrongly) also blocked that direct access, caught before
-- anything downstream depended on it and fixed here rather than in a
-- follow-up file.
alter table profiles add column if not exists is_reviewer boolean not null default false;

create or replace function protect_is_reviewer()
returns trigger
language plpgsql
as $$
begin
  if new.is_reviewer is distinct from old.is_reviewer
     and current_user in ('anon', 'authenticated') then
    raise exception 'is_reviewer can only be changed outside the anon/authenticated API roles -- e.g. from the SQL editor';
  end if;
  return new;
end;
$$;

drop trigger if exists profiles_protect_is_reviewer on profiles;
create trigger profiles_protect_is_reviewer
  before update on profiles
  for each row execute function protect_is_reviewer();

-- Never had a foreign key at all, unlike payment_submissions.reviewed_by
-- (0006), which points at the same table. Safe to add now: the column has
-- been unpopulated since it was created, so there is nothing to violate it.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'questions_reviewed_by_fk'
  ) then
    alter table questions
      add constraint questions_reviewed_by_fk
      foreign key (reviewed_by) references profiles(id);
  end if;
end
$$;
