create extension if not exists vector with schema extensions;

set search_path = public, extensions;

create table if not exists public.pknu_notice_sources (
    id text primary key,
    source_slug text unique not null,
    source_type text not null default 'unknown',
    title text,
    url text,
    parent_url text,
    file_path text,
    file_ext text,
    category text,
    subcategory text,
    status text not null default 'active',
    metadata jsonb not null default '{}'::jsonb,
    crawled_at timestamptz,
    processed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.pknu_notice_chunks (
    id text primary key,
    source_id text not null references public.pknu_notice_sources(id) on delete cascade,
    chunk_id integer not null,
    chunk_index integer not null,
    content text not null,
    content_hash text,
    token_count integer,
    metadata jsonb not null default '{}'::jsonb,
    embedding extensions.vector(384) not null,
    embedding_model text not null default 'sentence-transformers:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    embedding_dim integer not null default 384,
    embedded_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source_id, chunk_index)
);

create index if not exists pknu_notice_sources_source_slug_idx
    on public.pknu_notice_sources (source_slug);

create index if not exists pknu_notice_sources_status_idx
    on public.pknu_notice_sources (status);

create index if not exists pknu_notice_sources_metadata_gin_idx
    on public.pknu_notice_sources using gin (metadata);

create index if not exists pknu_notice_chunks_source_id_idx
    on public.pknu_notice_chunks (source_id);

create index if not exists pknu_notice_chunks_metadata_gin_idx
    on public.pknu_notice_chunks using gin (metadata);

create index if not exists pknu_notice_chunks_embedding_hnsw_idx
    on public.pknu_notice_chunks
    using hnsw (embedding vector_cosine_ops);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists pknu_notice_sources_set_updated_at on public.pknu_notice_sources;
create trigger pknu_notice_sources_set_updated_at
before update on public.pknu_notice_sources
for each row
execute function public.set_updated_at();

drop trigger if exists pknu_notice_chunks_set_updated_at on public.pknu_notice_chunks;
create trigger pknu_notice_chunks_set_updated_at
before update on public.pknu_notice_chunks
for each row
execute function public.set_updated_at();

create or replace function public.match_pknu_notice_documents(
    query_embedding extensions.vector(384),
    match_count integer default 5,
    min_similarity double precision default 0.0,
    metadata_filter jsonb default '{}'::jsonb
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
    similarity double precision
)
language sql
stable
as $$
    select
        c.id,
        c.source_id,
        s.source_slug,
        s.source_type,
        s.title,
        s.url,
        s.parent_url,
        c.chunk_id,
        c.chunk_index,
        c.content,
        s.metadata || c.metadata as metadata,
        1.0 - (c.embedding <=> query_embedding) as similarity
    from public.pknu_notice_chunks as c
    join public.pknu_notice_sources as s on s.id = c.source_id
    where s.status = 'active'
      and (s.metadata || c.metadata) @> metadata_filter
      and 1.0 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;
