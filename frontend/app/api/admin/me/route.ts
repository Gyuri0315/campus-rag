import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const UPSTREAM_TIMEOUT_MS = 10_000;

// 로그인한 사용자가 관리자인지 묻는 프록시. 홈의 Admin 버튼 노출 여부에만 쓴다.
// 실패하면 관리자가 아닌 것으로 답해서 버튼을 숨긴다(대시보드 자체는 백엔드가 따로 막는다).
export async function GET(request: NextRequest) {
  const authHeader = request.headers.get("authorization");
  if (!authHeader) {
    return Response.json({ isAdmin: false });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const upstream = await fetch(`${BACKEND_URL}/admin/me`, {
      headers: { Accept: "application/json", Authorization: authHeader },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!upstream.ok || !upstream.headers.get("Content-Type")?.includes("application/json")) {
      await upstream.body?.cancel();
      return Response.json({ isAdmin: false });
    }
    const body = (await upstream.json()) as { isAdmin?: unknown };
    return Response.json({ isAdmin: body.isAdmin === true });
  } catch {
    return Response.json({ isAdmin: false });
  } finally {
    clearTimeout(timeout);
  }
}
