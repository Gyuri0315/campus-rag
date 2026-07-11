// Route Handler / 서버 컴포넌트 전용 Supabase 클라이언트.
//
// Next.js 16 의 `cookies()` 는 Promise 를 반환하므로 호출부에서 await 한 cookie store 를
// 어댑터로 넘긴다. `getAll`/`setAll` 두 메서드만 구현하면 @supabase/ssr 가 알아서 동기화한다.
//
// 사용 예 (auth/callback/route.ts):
//   const cookieStore = await cookies();
//   const supabase = createServerSupabaseClient(cookieStore);
//   await supabase.auth.exchangeCodeForSession(code);

import { createServerClient, type CookieOptions } from "@supabase/ssr";

type CookieStore = {
  getAll: () => { name: string; value: string }[];
  set: (
    name: string,
    value: string,
    options?: CookieOptions,
  ) => void;
};

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error(
    "Supabase 환경변수가 누락됐습니다. NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY 를 .env.local 에 설정하세요.",
  );
}

export function createServerSupabaseClient(cookieStore: CookieStore) {
  return createServerClient(SUPABASE_URL!, SUPABASE_ANON_KEY!, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        // 서버 컴포넌트(렌더링 중)에서는 cookieStore.set 이 throw 할 수 있음.
        // Route Handler 에서만 실제로 set 되도록 try/catch 로 감싼다.
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // 서버 컴포넌트 컨텍스트 — 무시해도 안전.
        }
      },
    },
  });
}
