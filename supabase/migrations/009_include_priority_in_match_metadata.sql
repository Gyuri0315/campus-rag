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
        s.metadata
            || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        1.0 - (c.embedding <=> query_embedding) as similarity
    from public.rag_chunks as c
    join public.rag_sources as s on s.id = c.source_id
    where s.status = 'active'
      and (s.metadata || c.metadata) @> metadata_filter
      and 1.0 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;

create or replace function public.match_rule_documents(
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
        s.metadata
            || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        1.0 - (c.embedding <=> query_embedding) as similarity
    from public.rule_chunks as c
    join public.rule_sources as s on s.id = c.source_id
    where s.status = 'active'
      and (s.metadata || c.metadata) @> metadata_filter
      and 1.0 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;

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
        s.metadata
            || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        1.0 - (c.embedding <=> query_embedding) as similarity
    from public.pknu_notice_chunks as c
    join public.pknu_notice_sources as s on s.id = c.source_id
    where s.status = 'active'
      and (s.metadata || c.metadata) @> metadata_filter
      and 1.0 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;

create or replace function public.match_pknu_student_life_documents(
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
        s.metadata
            || c.metadata
            || jsonb_build_object(
                'priority_score', coalesce(s.priority_score, 0.0),
                'priority_details', coalesce(s.priority_details, '{}'::jsonb)
            ) as metadata,
        1.0 - (c.embedding <=> query_embedding) as similarity
    from public.pknu_student_life_chunks as c
    join public.pknu_student_life_sources as s on s.id = c.source_id
    where s.status = 'active'
      and (s.metadata || c.metadata) @> metadata_filter
      and 1.0 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit greatest(match_count, 0);
$$;
