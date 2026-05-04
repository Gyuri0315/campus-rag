create extension if not exists vector with schema extensions;

set search_path = public, extensions;

create table if not exists public.rag_documents (
    id text primary key,
    source_slug text not null,
    chunk_id integer not null,
    chunk_index integer not null,
    content text not null,
    metadata jsonb not null default '{}'::jsonb,
    embedding extensions.vector(384) not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists rag_documents_source_slug_idx
    on public.rag_documents (source_slug);

create index if not exists rag_documents_metadata_gin_idx
    on public.rag_documents using gin (metadata);

create index if not exists rag_documents_embedding_hnsw_idx
    on public.rag_documents
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

drop trigger if exists rag_documents_set_updated_at on public.rag_documents;

create trigger rag_documents_set_updated_at
before update on public.rag_documents
for each row
execute function public.set_updated_at();

create or replace function public.match_rag_documents(
    query_embedding extensions.vector(384),
    match_count integer default 5,
    min_similarity double precision default 0.0,
    metadata_filter jsonb default '{}'::jsonb
)
returns table (
    id text,
    source_slug text,
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
        d.id,
        d.source_slug,
        d.chunk_id,
        d.chunk_index,
        d.content,
        d.metadata,
        1.0 - (d.embedding <=> query_embedding) as similarity
    from public.rag_documents as d
    where d.metadata @> metadata_filter
      and 1.0 - (d.embedding <=> query_embedding) >= min_similarity
    order by d.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;
