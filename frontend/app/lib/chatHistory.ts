// Supabase-backed chat persistence helpers (RLS: anon JWT scoped by auth.uid()).

import { supabase } from "@/app/lib/supabase/client";
import type { ChatSource } from "@/app/lib/api";

export interface ChatRow {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageRow {
  id: string;
  chat_id: string;
  role: "user" | "assistant";
  content: string;
  sources: unknown;
  created_at: string;
}

/** Sidebar 등에서 사용하는 형태로 채팅 목록 조회 */
export async function listChats(): Promise<ChatRow[]> {
  const { data, error } = await supabase
    .from("chats")
    .select("id, title, created_at, updated_at")
    .order("updated_at", { ascending: false });

  if (error) throw error;
  return (data ?? []) as ChatRow[];
}

export async function createChat(title: string, userId: string): Promise<ChatRow> {
  const { data, error } = await supabase
    .from("chats")
    .insert({ user_id: userId, title })
    .select("id, title, created_at, updated_at")
    .single();

  if (error) throw error;
  return data as ChatRow;
}

export async function insertChatMessage(
  chatId: string,
  role: "user" | "assistant",
  content: string,
  sources: ChatSource[] | null,
): Promise<void> {
  const { error } = await supabase.from("chat_messages").insert({
    chat_id: chatId,
    role,
    content,
    sources,
  });

  if (error) throw error;
}

export async function fetchChatMessages(chatId: string): Promise<ChatMessageRow[]> {
  const { data, error } = await supabase
    .from("chat_messages")
    .select("id, chat_id, role, content, sources, created_at")
    .eq("chat_id", chatId)
    .order("created_at", { ascending: true });

  if (error) throw error;
  return (data ?? []) as ChatMessageRow[];
}

export async function updateChatTitle(chatId: string, title: string): Promise<void> {
  const { error } = await supabase.from("chats").update({ title }).eq("id", chatId);

  if (error) throw error;
}

export async function deleteChat(chatId: string): Promise<void> {
  const { error } = await supabase.from("chats").delete().eq("id", chatId);

  if (error) throw error;
}

/** DB jsonb → UI 출처 배열 (역직렬화 실패 시 빈 배열) */
export function parseStoredSources(raw: unknown): ChatSource[] | undefined {
  if (raw == null) return undefined;
  if (!Array.isArray(raw)) return undefined;
  const out: ChatSource[] = [];
  for (let i = 0; i < raw.length; i++) {
    const s = raw[i];
    if (!s || typeof s !== "object") continue;
    const o = s as Record<string, unknown>;
    out.push({
      id: typeof o.id === "number" ? o.id : i + 1,
      title: typeof o.title === "string" ? o.title : "(제목 없음)",
      category: typeof o.category === "string" ? o.category : "자료",
      chipMeta: typeof o.chipMeta === "string" ? o.chipMeta : undefined,
      formatChip:
        o.formatChip === "WEB" || o.formatChip === "PDF" || o.formatChip === "HWP"
          ? o.formatChip
          : undefined,
      quote: typeof o.quote === "string" ? o.quote : "",
      quoteSource: typeof o.quoteSource === "string" ? o.quoteSource : undefined,
      url: typeof o.url === "string" ? o.url : "#",
      attachments: Array.isArray(o.attachments)
        ? (o.attachments as { name: string; url: string }[])
        : [],
    });
  }
  return out.length > 0 ? out : undefined;
}
