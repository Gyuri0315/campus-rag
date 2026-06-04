alter table public.pknu_notice_sources
    add column if not exists priority_score double precision not null default 0.0,
    add column if not exists priority_details jsonb not null default '{}'::jsonb,
    add column if not exists priority_updated_at timestamptz;

alter table public.pknu_student_life_sources
    add column if not exists priority_score double precision not null default 0.0,
    add column if not exists priority_details jsonb not null default '{}'::jsonb,
    add column if not exists priority_updated_at timestamptz;

create index if not exists pknu_notice_sources_priority_score_idx
    on public.pknu_notice_sources (priority_score desc);

create index if not exists pknu_student_life_sources_priority_score_idx
    on public.pknu_student_life_sources (priority_score desc);

