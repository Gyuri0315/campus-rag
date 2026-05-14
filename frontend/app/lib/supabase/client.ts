// 브라우저 전용 Supabase 클라이언트.
// `@supabase/ssr` 의 createBrowserClient 는 세션 쿠키를 자동으로 읽고 써서
// 서버(Route Handler / 서버 컴포넌트) 와 같은 세션을 공유한다.
//
// 모듈 스코프 싱글톤이라 같은 페이지 안에서 여러 번 호출해도 인스턴스는 하나만 만들어진다.

import { createBrowserClient } from "@supabase/ssr";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // 빌드 타임에 누락되면 앱이 어차피 동작하지 않으니 즉시 실패시킨다.
  throw new Error(
    "Supabase 환경변수가 누락됐습니다. NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY 를 .env.local 에 설정하세요.",
  );
}

export const supabase = createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY);
