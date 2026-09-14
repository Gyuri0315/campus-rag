set search_path = public;

-- ── BM25-style lexical search (hybrid retrieval) ─────────────────────────
-- The pure vector search misses exact-term matches badly for formal Korean
-- administrative text: e.g. for "복수전공 신청 조건", the one chunk that
-- actually answers it ranks ~160th by cosine similarity among rule_chunks,
-- because generic bureaucratic boilerplate ("이 지침은 ... 규정함을
-- 목적으로 한다" 등) embeds deceptively close to almost any formal-Korean
-- query. A keyword/full-text signal catches exact terms like "복수전공"
-- that the embedding model does not discriminate well, and gets combined
-- with the vector results via Reciprocal Rank Fusion in the Python layer
-- (see backend/app/retrieval.py).
--
-- Postgres has no built-in Korean dictionary, so we use the 'simple'
-- text search configuration (whitespace/punctuation tokenization, no
-- stemming) rather than pretending to have Korean morphological analysis.
-- This still matches whole tokens like "복수전공" exactly, which is what we
-- need here; it does not handle particle-attached forms ("복수전공을") as
-- the same token, which is an accepted limitation of this pass.

alter table public.rule_chunks
    add column if not exists content_tsv tsvector
    generated always as (to_tsvector('simple', coalesce(content, ''))) stored;
create index if not exists rule_chunks_tsv_idx on public.rule_chunks using gin (content_tsv);

alter table public.rag_chunks
    add column if not exists content_tsv tsvector
    generated always as (to_tsvector('simple', coalesce(content, ''))) stored;
create index if not exists rag_chunks_tsv_idx on public.rag_chunks using gin (content_tsv);

alter table public.pknu_notice_chunks
    add column if not exists content_tsv tsvector
    generated always as (to_tsvector('simple', coalesce(content, ''))) stored;
create index if not exists pknu_notice_chunks_tsv_idx on public.pknu_notice_chunks using gin (content_tsv);

alter table public.pknu_student_life_chunks
    add column if not exists content_tsv tsvector
    generated always as (to_tsvector('simple', coalesce(content, ''))) stored;
create index if not exists pknu_student_life_chunks_tsv_idx on public.pknu_student_life_chunks using gin (content_tsv);

-- ── Lexical match RPCs (one per dataset, mirrors the *_documents naming) ─
create or replace function public.match_rule_documents_lexical(
    query_text text,
    match_count integer default 30
)
returns table (
    id text,
    source_id text,
    source_slug text,
    source_type text,
    title text,
    url text,
    parent_url text,
    chunk_id integer,
    chunk_index integer,
    content text,
    metadata jsonb,
    lexical_rank double precision
)
language sql
stable
as $$
    select
        c.id, c.source_id, s.source_slug, s.source_type, s.title, s.url, s.parent_url,
        c.chunk_id, c.chunk_index, c.content,
        s.metadata || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', query_text)) as lexical_rank
    from public.rule_chunks as c
    join public.rule_sources as s on s.id = c.source_id
    where s.status = 'active'
      and c.content_tsv @@ websearch_to_tsquery('simple', query_text)
    order by lexical_rank desc
    limit greatest(match_count, 0);
$$;

create or replace function public.match_rag_documents_lexical(
    query_text text,
    match_count integer default 30
)
returns table (
    id text,
    source_id text,
    source_slug text,
    source_type text,
    title text,
    url text,
    parent_url text,
    chunk_id integer,
    chunk_index integer,
    content text,
    metadata jsonb,
    lexical_rank double precision
)
language sql
stable
as $$
    select
        c.id, c.source_id, s.source_slug, s.source_type, s.title, s.url, s.parent_url,
        c.chunk_id, c.chunk_index, c.content,
        s.metadata || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', query_text)) as lexical_rank
    from public.rag_chunks as c
    join public.rag_sources as s on s.id = c.source_id
    where s.status = 'active'
      and c.content_tsv @@ websearch_to_tsquery('simple', query_text)
    order by lexical_rank desc
    limit greatest(match_count, 0);
$$;

create or replace function public.match_pknu_notice_documents_lexical(
    query_text text,
    match_count integer default 30
)
returns table (
    id text,
    source_id text,
    source_slug text,
    source_type text,
    title text,
    url text,
    parent_url text,
    chunk_id integer,
    chunk_index integer,
    content text,
    metadata jsonb,
    lexical_rank double precision
)
language sql
stable
as $$
    select
        c.id, c.source_id, s.source_slug, s.source_type, s.title, s.url, s.parent_url,
        c.chunk_id, c.chunk_index, c.content,
        s.metadata || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', query_text)) as lexical_rank
    from public.pknu_notice_chunks as c
    join public.pknu_notice_sources as s on s.id = c.source_id
    where s.status = 'active'
      and c.content_tsv @@ websearch_to_tsquery('simple', query_text)
    order by lexical_rank desc
    limit greatest(match_count, 0);
$$;

create or replace function public.match_pknu_student_life_documents_lexical(
    query_text text,
    match_count integer default 30
)
returns table (
    id text,
    source_id text,
    source_slug text,
    source_type text,
    title text,
    url text,
    parent_url text,
    chunk_id integer,
    chunk_index integer,
    content text,
    metadata jsonb,
    lexical_rank double precision
)
language sql
stable
as $$
    select
        c.id, c.source_id, s.source_slug, s.source_type, s.title, s.url, s.parent_url,
        c.chunk_id, c.chunk_index, c.content,
        s.metadata || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', query_text)) as lexical_rank
    from public.pknu_student_life_chunks as c
    join public.pknu_student_life_sources as s on s.id = c.source_id
    where s.status = 'active'
      and c.content_tsv @@ websearch_to_tsquery('simple', query_text)
    order by lexical_rank desc
    limit greatest(match_count, 0);
$$;
