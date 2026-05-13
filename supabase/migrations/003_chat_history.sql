set search_path = public;

-- ── chats ─────────────────────────────────────────────────────────────
create table if not exists public.chats (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    title text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists chats_user_id_updated_at_idx
    on public.chats (user_id, updated_at desc);

drop trigger if exists chats_set_updated_at on public.chats;
create trigger chats_set_updated_at
before update on public.chats
for each row execute function public.set_updated_at();

-- ── chat_messages ──────────────────────────────────────────────────────
create table if not exists public.chat_messages (
    id uuid primary key default gen_random_uuid(),
    chat_id uuid not null references public.chats (id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    sources jsonb,
    created_at timestamptz not null default now()
);

create index if not exists chat_messages_chat_id_created_at_idx
    on public.chat_messages (chat_id, created_at asc);

-- 새 메시지마다 부모 채팅의 updated_at 갱신 (목록 정렬용)
create or replace function public.touch_chat_updated_at()
returns trigger
language plpgsql
as $$
begin
    update public.chats
    set updated_at = now()
    where id = new.chat_id;
    return new;
end;
$$;

drop trigger if exists chat_messages_touch_chat on public.chat_messages;
create trigger chat_messages_touch_chat
after insert on public.chat_messages
for each row execute function public.touch_chat_updated_at();

-- ── RLS ────────────────────────────────────────────────────────────────
alter table public.chats enable row level security;
alter table public.chat_messages enable row level security;

drop policy if exists chats_select_own on public.chats;
create policy chats_select_own on public.chats
    for select using (auth.uid() = user_id);

drop policy if exists chats_insert_own on public.chats;
create policy chats_insert_own on public.chats
    for insert with check (auth.uid() = user_id);

drop policy if exists chats_update_own on public.chats;
create policy chats_update_own on public.chats
    for update using (auth.uid() = user_id);

drop policy if exists chats_delete_own on public.chats;
create policy chats_delete_own on public.chats
    for delete using (auth.uid() = user_id);

drop policy if exists chat_messages_select_own on public.chat_messages;
create policy chat_messages_select_own on public.chat_messages
    for select using (
        exists (
            select 1 from public.chats c
            where c.id = chat_messages.chat_id and c.user_id = auth.uid()
        )
    );

drop policy if exists chat_messages_insert_own on public.chat_messages;
create policy chat_messages_insert_own on public.chat_messages
    for insert with check (
        exists (
            select 1 from public.chats c
            where c.id = chat_messages.chat_id and c.user_id = auth.uid()
        )
    );

drop policy if exists chat_messages_update_own on public.chat_messages;
create policy chat_messages_update_own on public.chat_messages
    for update using (
        exists (
            select 1 from public.chats c
            where c.id = chat_messages.chat_id and c.user_id = auth.uid()
        )
    );

drop policy if exists chat_messages_delete_own on public.chat_messages;
create policy chat_messages_delete_own on public.chat_messages
    for delete using (
        exists (
            select 1 from public.chats c
            where c.id = chat_messages.chat_id and c.user_id = auth.uid()
        )
    );
