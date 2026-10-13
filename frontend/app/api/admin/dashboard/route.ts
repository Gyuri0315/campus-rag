import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const UPSTREAM_TIMEOUT_MS = 30_000;

// 관리자 대시보드 데이터 프록시. 권한 판정(관리자 이메일 허용 목록)은 백엔드가 한다.
export async function GET(request: NextRequest) {
  const authHeader = request.headers.get("authorization");
  if (!authHeader) {
    return Response.json({ detail: "로그인이 필요합니다." }, { status: 401 });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const upstream = await fetch(`${BACKEND_URL}/admin/dashboard`, {
      headers: { Accept: "application/json", Authorization: authHeader },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!upstream.headers.get("Content-Type")?.includes("application/json")) {
      await upstream.body?.cancel();
      return Response.json({ detail: "백엔드 응답 형식이 올바르지 않습니다." }, { status: 502 });
    }
    return Response.json(await upstream.json(), { status: upstream.status });
  } catch {
    if (controller.signal.aborted) {
      return Response.json({ detail: "백엔드 응답이 너무 오래 걸립니다." }, { status: 504 });
    }
    return Response.json({ detail: "백엔드에 연결할 수 없습니다." }, { status: 502 });
  } finally {
    clearTimeout(timeout);
  }
}
