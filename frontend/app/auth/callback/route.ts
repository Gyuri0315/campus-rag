// OAuth / 이메일 확인 redirect 후 도착하는 콜백 라우트.
//
// Supabase 가 ?code=... 쿼리로 돌려보내면 그것을 세션 쿠키로 교환한다.
// 교환이 끝나면 next 쿼리(원래 가려던 곳) 또는 / 로 redirect.

import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";

import { createServerSupabaseClient } from "@/app/lib/supabase/server";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  // open redirect 방어: 외부 URL 로 못 튀게 path 만 허용.
  const rawNext = searchParams.get("next") ?? "/";
  const nextPath = rawNext.startsWith("/") ? rawNext : "/";

  // OAuth provider 가 에러를 직접 돌려주는 케이스 (사용자가 동의 거부 등)
  const oauthError = searchParams.get("error_description") ?? searchParams.get("error");
  if (oauthError) {
    const url = new URL("/auth", origin);
    url.searchParams.set("error", oauthError);
    return NextResponse.redirect(url);
  }

  if (code) {
    const cookieStore = await cookies();
    const supabase = createServerSupabaseClient(cookieStore);
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) {
      const url = new URL("/auth", origin);
      url.searchParams.set("error", error.message);
      return NextResponse.redirect(url);
    }
  }

  return NextResponse.redirect(new URL(nextPath, origin));
}
