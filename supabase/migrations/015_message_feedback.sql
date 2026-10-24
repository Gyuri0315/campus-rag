set search_path = public;

-- ── Per-answer user feedback (👍 / 👎 + reasons) ──────────────────────────
-- One row per (assistant message, user). Switching 👍 <-> 👎 updates the same
-- row (upsert on the unique key); cancelling deletes it. Reasons/comment are
-- only meaningful for 👎 and are cleared by the client when switching to 👍.
-- Feeds the "user feedback -> RAG improvement" loop (admin dashboard later).

create table if not exists public.message_feedback (
    id uuid primary key default gen_random_uuid(),
    message_id uuid not null references public.chat_messages (id) on delete cascade,
    user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
    rating text not null check (rating in ('up', 'down')),
    reasons text[] not null default '{}',
    comment text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint message_feedback_one_per_user unique (message_id, user_id),
    constraint message_feedback_reasons_limit check (cardinality(reasons) <= 10),
    constraint message_feedback_comment_length check (comment is null or char_length(comment) <= 1000),
    constraint message_feedback_details_only_for_down check (
        rating = 'down' or (cardinality(reasons) = 0 and comment is null)
    )
);

create index if not exists message_feedback_user_id_idx on public.message_feedback (user_id);
create index if not exists message_feedback_rating_created_at_idx
    on public.message_feedback (rating, created_at desc);

drop trigger if exists message_feedback_set_updated_at on public.message_feedback;
create trigger message_feedback_set_updated_at
before update on public.message_feedback
for each row execute function public.set_updated_at();

-- ── RLS: a user sees and controls only their own feedback, and may only
--    rate assistant answers inside their own chats. ─────────────────────────
alter table public.message_feedback enable row level security;

drop policy if exists message_feedback_select_own on public.message_feedback;
create policy message_feedback_select_own on public.message_feedback
    for select to authenticated
    using (auth.uid() = user_id);

drop policy if exists message_feedback_insert_own on public.message_feedback;
create policy message_feedback_insert_own on public.message_feedback
    for insert to authenticated
    with check (
        auth.uid() = user_id
        and exists (
            select 1
            from public.chat_messages m
            join public.chats c on c.id = m.chat_id
            where m.id = message_feedback.message_id
              and m.role = 'assistant'
              and c.user_id = auth.uid()
        )
    );

drop policy if exists message_feedback_update_own on public.message_feedback;
create policy message_feedback_update_own on public.message_feedback
    for update to authenticated
    using (auth.uid() = user_id)
    with check (
        auth.uid() = user_id
        and exists (
            select 1
            from public.chat_messages m
            join public.chats c on c.id = m.chat_id
            where m.id = message_feedback.message_id
              and m.role = 'assistant'
              and c.user_id = auth.uid()
        )
    );

drop policy if exists message_feedback_delete_own on public.message_feedback;
create policy message_feedback_delete_own on public.message_feedback
    for delete to authenticated
    using (auth.uid() = user_id);

-- Supabase's default privileges hand every new public table to anon and
-- authenticated with ALL privileges, including TRUNCATE, which bypasses RLS.
-- Feedback needs a login, so anon gets nothing and authenticated only CRUD.
revoke all on public.message_feedback from anon;
revoke all on public.message_feedback from authenticated;
grant select, insert, update, delete on public.message_feedback to authenticated;
