const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const UPSTREAM_TIMEOUT_MS = 8_000;

// 홈·빈 채팅 화면의 "자주 묻는 질문" 프록시. 로그인 없이 쓴다.
// 실패하면 빈 목록을 돌려주고, 화면은 기본 질문을 보여준다.
export async function GET() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const upstream = await fetch(`${BACKEND_URL}/popular-questions`, {
      headers: { Accept: "application/json" },
      next: { revalidate: 600 },
      signal: controller.signal,
    });
    if (!upstream.ok || !upstream.headers.get("Content-Type")?.includes("application/json")) {
      await upstream.body?.cancel();
      return Response.json({ questions: [] });
    }
    const body = (await upstream.json()) as { questions?: unknown };
    const questions = Array.isArray(body.questions)
      ? body.questions
          .map((item) => (item as { question?: unknown })?.question)
          .filter((q): q is string => typeof q === "string" && q.trim().length > 0)
      : [];
    return Response.json({ questions });
  } catch {
    return Response.json({ questions: [] });
  } finally {
    clearTimeout(timeout);
  }
}
