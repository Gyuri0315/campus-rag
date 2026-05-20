alter table public.rag_sources
    add column if not exists priority_score double precision not null default 0.0,
    add column if not exists priority_details jsonb not null default '{}'::jsonb,
    add column if not exists priority_updated_at timestamptz;

alter table public.rule_sources
    add column if not exists priority_score double precision not null default 0.0,
    add column if not exists priority_details jsonb not null default '{}'::jsonb,
    add column if not exists priority_updated_at timestamptz;

create index if not exists rag_sources_priority_score_idx
    on public.rag_sources (priority_score desc);

create index if not exists rule_sources_priority_score_idx
    on public.rule_sources (priority_score desc);
