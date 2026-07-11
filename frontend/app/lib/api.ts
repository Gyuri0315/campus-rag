/**
 * Client-side helper for talking to the Next.js Route Handler at /api/ask,
 * which in turn proxies to the FastAPI backend.
 *
 * Also adapts the backend's lean Source shape ({title, uri, content, similarity})
 * into the richer Source shape the chat UI renders.
 */

export type Attachment = { name: string; url: string };

export type ChatSource = {
  id: number;
  title: string;
  category: string;
  chipMeta?: string;
  formatChip?: "WEB" | "PDF" | "HWP";
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
  similarity?: number;
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
    attachments: [],
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
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });

  if (!res.ok) {
    throw new Error(await readErrorMessage(res));
  }

  const data = (await res.json()) as BackendAskResponse;
  return {
    answer: (data.answer ?? "").trim(),
    sources: (data.sources ?? []).map(adaptSource),
  };
}
