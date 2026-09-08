#!/usr/bin/env python3
"""Apply db/migrations/*.sql in filename order, once each.

`psql -f` is still the documented path and does the same thing. This exists
because it is the only path that works everywhere the project is developed:
Windows has no psql unless PostgreSQL is installed locally, and the migrations
have to reach a hosted Supabase database either way.

It is a ledger, not a migration framework. There is no `down`, no generation,
no ORM: a migration is a file, applied once, recorded in `schema_migrations`
with the checksum of what was applied. Editing a file that has already run is
the one mistake this catches — it refuses rather than silently diverging from
the database it claims to describe.

    python db/apply.py               # 0001-0008, the portable schema
    python db/apply.py --supabase    # ... and 0009, which needs auth.users
    python db/apply.py --status      # what has been applied, change nothing
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

# References auth.users, which only exists on Supabase. Opt in with --supabase.
SUPABASE_ONLY = {"0009_supabase_auth.sql"}

LEDGER = """
create table if not exists schema_migrations (
  version    text primary key,
  checksum   text not null,
  applied_at timestamptz not null default now()
)
"""


def load_dotenv() -> None:
    """Read the repository root's .env, without clobbering the real environment."""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supabase", action="store_true",
                        help="Also apply migrations that require Supabase's auth schema.")
    parser.add_argument("--status", action="store_true",
                        help="Report what is applied and exit without writing.")
    args = parser.parse_args()

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL is not set (env or .env at the repository root).", file=sys.stderr)
        return 2

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"no migrations found in {MIGRATIONS}", file=sys.stderr)
        return 2

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(LEDGER)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("select version, checksum from schema_migrations")
            applied = dict(cur.fetchall())

        exit_code = 0
        for path in files:
            name, checksum = path.name, digest(path)

            if name in applied:
                if applied[name] == checksum:
                    print(f"  ok      {name}")
                else:
                    # The file on disk is no longer what the database ran. Fix by
                    # writing a new migration, not by editing the old one.
                    print(f"  CHANGED {name}  (applied {applied[name]}, on disk {checksum})")
                    exit_code = 1
                continue

            if name in SUPABASE_ONLY and not args.supabase:
                print(f"  skip    {name}  (--supabase)")
                continue

            if args.status:
                print(f"  pending {name}")
                continue

            # One transaction per file: a migration either lands whole or not at
            # all, and the ledger row commits with the change it describes.
            try:
                with conn.cursor() as cur:
                    cur.execute(path.read_text())
                    cur.execute(
                        "insert into schema_migrations (version, checksum) values (%s, %s)",
                        (name, checksum),
                    )
                conn.commit()
                print(f"  applied {name}")
            except psycopg.Error as error:
                conn.rollback()
                print(f"  FAILED  {name}\n{error}", file=sys.stderr)
                return 1

        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
