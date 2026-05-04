create extension if not exists vector with schema extensions;

set search_path = public, extensions;

create table if not exists public.rag_sources (
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

create table if not exists public.rag_chunks (
    id text primary key,
    source_id text not null references public.rag_sources(id) on delete cascade,
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

create index if not exists rag_sources_source_slug_idx
    on public.rag_sources (source_slug);

create index if not exists rag_sources_status_idx
    on public.rag_sources (status);

create index if not exists rag_sources_metadata_gin_idx
    on public.rag_sources using gin (metadata);

create index if not exists rag_chunks_source_id_idx
    on public.rag_chunks (source_id);

create index if not exists rag_chunks_metadata_gin_idx
    on public.rag_chunks using gin (metadata);

create index if not exists rag_chunks_embedding_hnsw_idx
    on public.rag_chunks
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

drop trigger if exists rag_sources_set_updated_at on public.rag_sources;
create trigger rag_sources_set_updated_at
before update on public.rag_sources
for each row
execute function public.set_updated_at();

drop trigger if exists rag_chunks_set_updated_at on public.rag_chunks;
create trigger rag_chunks_set_updated_at
before update on public.rag_chunks
for each row
execute function public.set_updated_at();

insert into public.rag_sources (
    id,
    source_slug,
    source_type,
    title,
    url,
    parent_url,
    file_path,
    file_ext,
    category,
    subcategory,
    metadata,
    created_at,
    updated_at
)
select distinct on (d.source_slug)
    d.source_slug as id,
    d.source_slug,
    coalesce(nullif(d.metadata->>'source_ext', ''), 'unknown') as source_type,
    nullif(d.metadata->>'doc_title', '') as title,
    nullif(coalesce(d.metadata->>'doc_url', d.metadata->>'attachment_url'), '') as url,
    nullif(d.metadata->>'source_page_url', '') as parent_url,
    nullif(d.metadata->>'source_path', '') as file_path,
    nullif(d.metadata->>'source_ext', '') as file_ext,
    nullif(d.metadata->>'category', '') as category,
    nullif(d.metadata->>'subcategory', '') as subcategory,
    d.metadata,
    min(d.created_at) over (partition by d.source_slug),
    max(d.updated_at) over (partition by d.source_slug)
from public.rag_documents as d
where to_regclass('public.rag_documents') is not null
on conflict (id) do update set
    source_slug = excluded.source_slug,
    source_type = excluded.source_type,
    title = excluded.title,
    url = excluded.url,
    parent_url = excluded.parent_url,
    file_path = excluded.file_path,
    file_ext = excluded.file_ext,
    category = excluded.category,
    subcategory = excluded.subcategory,
    metadata = excluded.metadata,
    updated_at = excluded.updated_at;

insert into public.rag_chunks (
    id,
    source_id,
    chunk_id,
    chunk_index,
    content,
    metadata,
    embedding,
    created_at,
    updated_at
)
select
    d.id,
    d.source_slug as source_id,
    d.chunk_id,
    d.chunk_index,
    d.content,
    d.metadata,
    d.embedding,
    d.created_at,
    d.updated_at
from public.rag_documents as d
where to_regclass('public.rag_documents') is not null
on conflict (id) do update set
    source_id = excluded.source_id,
    chunk_id = excluded.chunk_id,
    chunk_index = excluded.chunk_index,
    content = excluded.content,
    metadata = excluded.metadata,
    embedding = excluded.embedding,
    updated_at = excluded.updated_at;

drop trigger if exists rag_documents_set_updated_at on public.rag_documents;
drop function if exists public.match_rag_documents(
    extensions.vector,
    integer,
    double precision,
    jsonb
);
drop table if exists public.rag_documents;

create or replace function public.match_rag_documents(
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
    from public.rag_chunks as c
    join public.rag_sources as s on s.id = c.source_id
    where s.status = 'active'
      and (s.metadata || c.metadata) @> metadata_filter
      and 1.0 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;
