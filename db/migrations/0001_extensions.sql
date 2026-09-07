-- 0001_extensions.sql
-- Extensions used across the schema.

create extension if not exists "pgcrypto";   -- gen_random_uuid()
create extension if not exists "vector";     -- pgvector, for retrieval (0007)
create extension if not exists "pg_trgm";    -- trigram search over question text
