import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const UPSTREAM_TIMEOUT_MS = 60_000;

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json(
      { detail: "request body must be valid JSON" },
      { status: 400 },
    );
  }

  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return Response.json(
      { detail: "`question` must be a non-empty string" },
      { status: 400 },
    );
  }

  // AbortController bridges the browser-aborted request to the upstream fetch
  // and also enforces a hard timeout so a hung backend can't hang Next.js.
  const controller = new AbortController();
  const onClientAbort = () => controller.abort();
  request.signal.addEventListener("abort", onClientAbort);
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${BACKEND_URL}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question.trim() }),
      cache: "no-store",
      signal: controller.signal,
    });

    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch (err) {
    if (controller.signal.aborted && !request.signal.aborted) {
      return Response.json(
        { detail: "백엔드 응답이 너무 오래 걸립니다." },
        { status: 504 },
      );
    }
    console.error("[/api/ask] proxy failed:", err);
    return Response.json(
      { detail: "백엔드에 연결할 수 없습니다." },
      { status: 502 },
    );
  } finally {
    clearTimeout(timeout);
    request.signal.removeEventListener("abort", onClientAbort);
  }
}
