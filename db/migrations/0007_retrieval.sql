-- 0007_retrieval.sql
-- The retrieval index behind topical search and the AI solver.
--
-- pgvector rather than a dedicated vector database, because the query that
-- actually matters is hybrid: "questions similar to this one, *restricted to*
-- this subject and this syllabus version and approved status". Keeping vectors
-- in the same database as that metadata makes it one indexed query instead of a
-- fan-out plus a join in application code.
--
-- 1024 dimensions matches voyage-3. Change the literal here if you change
-- NOTEACADEMY_EMBEDDING_MODEL — and re-embed; you cannot mix dimensionalities.

create table question_embeddings (
  question_id  uuid primary key references questions(id) on delete cascade,
  embedding    vector(1024) not null,
  -- sha256 of the exact text that was embedded. Lets a re-run skip questions
  -- whose text has not changed instead of re-billing the whole corpus.
  content_hash text not null,
  model        text not null,
  created_at   timestamptz not null default now()
);

create index question_embeddings_hnsw
  on question_embeddings using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- Mark schemes and examiner reports chunked for the solver's grounded context.
-- Kept separate from question_embeddings: a question is retrieved to *identify*
-- what the student is asking about, a chunk is retrieved to *ground* the answer.
create type chunk_source as enum ('mark_scheme', 'examiner_report', 'syllabus', 'note');

create table content_chunks (
  id           uuid primary key default gen_random_uuid(),
  source       chunk_source not null,
  subject_id   uuid references subjects(id) on delete cascade,
  paper_id     uuid references papers(id) on delete cascade,
  question_id  uuid references questions(id) on delete cascade,
  topic_id     uuid references topics(id) on delete set null,
  content      text not null,
  embedding    vector(1024),
  content_hash text not null,
  created_at   timestamptz not null default now()
);

create index content_chunks_hnsw
  on content_chunks using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
create index content_chunks_subject_idx on content_chunks (subject_id, source);

-- Identify which bank question a pasted/screenshotted question actually *is*.
--
-- This is the step that keeps the solver honest. If the match clears the
-- threshold we do not do retrieval-augmented generation at all — we look up that
-- question's own mark scheme and examiner report and hand them to the model
-- verbatim. RAG over chunks is only the fallback for questions not in the bank.
create or replace function match_question(
  p_embedding  vector(1024),
  p_subject_id uuid default null,
  p_threshold  real default 0.82,
  p_limit      int  default 5
)
returns table (
  question_id uuid,
  similarity  real,
  display_label text,
  paper_slug  text
)
language sql stable
as $$
  select
    qe.question_id,
    (1 - (qe.embedding <=> p_embedding))::real as similarity,
    q.display_label,
    p.slug
  from question_embeddings qe
  join questions q on q.id = qe.question_id
  join papers    p on p.id = q.paper_id
  where q.extraction_status = 'approved'
    and (p_subject_id is null or p.subject_id = p_subject_id)
    and (1 - (qe.embedding <=> p_embedding)) >= p_threshold
  order by qe.embedding <=> p_embedding
  limit p_limit;
$$;
