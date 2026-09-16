/**
 * Client-side helper for talking to the Next.js Route Handler at /api/ask,
 * which in turn proxies to the FastAPI backend.
 *
 * Also adapts the backend's lean Source shape ({title, uri, content})
 * into the richer Source shape the chat UI renders.
 */

import { supabase } from "@/app/lib/supabase/client";

export type Attachment = { name: string; url: string };

export type ChatSource = {
  id: number;
  title: string;
  category: string;
  chipMeta?: string;
  quote: string;
  quoteSource?: string;
  url: string;
  attachments: Attachment[];
};

export type AskResult = {
  answer: string;
  sources: ChatSource[];
};

type BackendSource = {
  title?: string;
  uri?: string;
  content?: string;
  attachments?: Array<{ name?: unknown; url?: unknown }>;
};

type BackendAskResponse = {
  answer?: string;
  sources?: BackendSource[];
};

const DEFAULT_CATEGORY = "자료";

function adaptSource(src: BackendSource, index: number): ChatSource {
  return {
    // 1-based id keeps the answer's [1][2] markers aligned with the
    // backend's source order.
    id: index + 1,
    title: (src.title ?? "").trim() || "(제목 없음)",
    category: DEFAULT_CATEGORY,
    quote: (src.content ?? "").trim(),
    url: (src.uri ?? "").trim() || "#",
    attachments: (src.attachments ?? []).flatMap((attachment) => {
      const name = typeof attachment.name === "string" ? attachment.name.trim() : "";
      const url = typeof attachment.url === "string" ? attachment.url.trim() : "";
      return name && url ? [{ name, url }] : [];
    }),
  };
}

async function readErrorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown; error?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
    if (typeof body.error === "string" && body.error.trim()) return body.error;
  } catch {
    // body wasn't JSON — fall through to status-based message
  }
  return `요청 실패 (HTTP ${res.status})`;
}

export async function askBackend(
  question: string,
  signal?: AbortSignal,
): Promise<AskResult> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    throw new Error("로그인이 필요합니다.");
  }

  const res = await fetch("/api/ask", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({ question }),
    signal,
  });

  if (!res.ok) {
    throw new Error(await readErrorMessage(res));
  }

  const data = (await res.json()) as BackendAskResponse;
  if (typeof data?.answer !== "string" || !Array.isArray(data.sources)) {
    throw new Error("답변 응답 형식이 올바르지 않습니다.");
  }
  return {
    answer: (data.answer ?? "").trim(),
    sources: (data.sources ?? []).map(adaptSource),
  };
}
