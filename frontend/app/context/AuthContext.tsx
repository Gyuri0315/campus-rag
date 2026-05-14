"use client";

// 전역 인증 상태 컨텍스트.
// - 마운트 시 현재 세션을 한 번 읽고, 그 뒤로는 onAuthStateChange 구독으로 동기화한다.
// - 액션(signIn / signUp / OAuth / signOut)은 Supabase SDK 호출을 얇게 래핑해서
//   호출부가 await 한 번으로 결과(에러/성공/이메일확인필요)를 알 수 있게 한다.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { Session, User } from "@supabase/supabase-js";

import { supabase } from "@/app/lib/supabase/client";

// ── OAuth 프로바이더 ──────────────────────────────────────────────────────────
// 현재 활성화된 것만 노출. (Supabase 대시보드에서 활성 처리 필요)
export type OAuthProvider = "google" | "kakao";

// ── 액션 결과 타입 ────────────────────────────────────────────────────────────
// 성공/실패만 구분하는 단순 형태. UI 가 에러 메시지를 그대로 띄울 수 있게 message 를 둠.
export interface AuthActionResult {
  ok: boolean;
  /** 사용자에게 보여줄 짧은 메시지 (성공 안내 또는 에러). */
  message?: string;
  /** 회원가입 시 이메일 확인 메일 발송 여부 — UI 분기에 사용. */
  emailConfirmationRequired?: boolean;
}

interface AuthContextValue {
  user: User | null;
  session: Session | null;
  /** 초기 세션 로딩 중 플래그 — 헤더 깜빡임 방지에 사용. */
  loading: boolean;

  signInWithPassword: (email: string, password: string) => Promise<AuthActionResult>;
  signUpWithPassword: (email: string, password: string) => Promise<AuthActionResult>;
  signInWithOAuth: (provider: OAuthProvider, redirectTo?: string) => Promise<AuthActionResult>;
  signOut: () => Promise<AuthActionResult>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// ── 에러 메시지 정규화 ────────────────────────────────────────────────────────
// Supabase 에러 메시지는 영어라 한국어로 짧게 매핑. 매칭 안 되면 원문 그대로.
function normalizeAuthError(message: string | undefined | null): string {
  if (!message) return "알 수 없는 오류가 발생했습니다.";
  const m = message.toLowerCase();
  if (m.includes("invalid login credentials")) {
    return "이메일 또는 비밀번호가 올바르지 않습니다.";
  }
  if (m.includes("email not confirmed")) {
    return "이메일 인증이 완료되지 않았습니다. 받은 메일을 확인해 주세요.";
  }
  if (m.includes("user already registered")) {
    return "이미 가입된 이메일입니다.";
  }
  if (m.includes("password should be at least")) {
    return "비밀번호는 6자 이상이어야 합니다.";
  }
  if (m.includes("rate limit") || m.includes("too many")) {
    return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.";
  }
  return message;
}

// ── OAuth 콜백 redirect 계산 ──────────────────────────────────────────────────
// 브라우저에서만 호출됨 (signInWithOAuth 내부). origin + /auth/callback + ?next=...
function buildOAuthRedirect(nextPath?: string): string {
  if (typeof window === "undefined") return "";
  const url = new URL("/auth/callback", window.location.origin);
  if (nextPath) url.searchParams.set("next", nextPath);
  return url.toString();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  // 마운트 시 현재 세션 로드 + 변경 구독.
  useEffect(() => {
    let active = true;

    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!active) return;
        setSession(data.session ?? null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    const { data: sub } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession ?? null);
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  // ── 액션 ────────────────────────────────────────────────────────────────────
  const signInWithPassword = useCallback(
    async (email: string, password: string): Promise<AuthActionResult> => {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) return { ok: false, message: normalizeAuthError(error.message) };
      return { ok: true };
    },
    [],
  );

  const signUpWithPassword = useCallback(
    async (email: string, password: string): Promise<AuthActionResult> => {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          // 이메일 확인 메일의 redirect 도 같은 콜백 라우트로 모은다.
          emailRedirectTo:
            typeof window !== "undefined"
              ? new URL("/auth/callback", window.location.origin).toString()
              : undefined,
        },
      });
      if (error) return { ok: false, message: normalizeAuthError(error.message) };

      // Supabase 는 이메일 확인이 켜져 있으면 session 을 null 로 반환하고 confirmation 메일을 보낸다.
      const emailConfirmationRequired = !data.session;
      return {
        ok: true,
        emailConfirmationRequired,
        message: emailConfirmationRequired
          ? "확인 메일을 보냈습니다. 메일함을 확인해 주세요."
          : undefined,
      };
    },
    [],
  );

  const signInWithOAuth = useCallback(
    async (provider: OAuthProvider, nextPath?: string): Promise<AuthActionResult> => {
      // NOTE — 카카오 scope 처리 관련.
      //
      // GoTrue 의 kakao 프로바이더는 기본 scope 인
      //   ["account_email", "profile_image", "profile_nickname"]
      // 를 하드코드해 두고, 클라이언트가 보낸 `scopes` 는 *replace* 가 아니라
      // *append* 한다. 즉 client 에서 scope 를 어떻게 보내도 account_email 은 항상 포함된다.
      //   참고: https://github.com/supabase/auth/blob/master/internal/api/provider/kakao.go
      //
      // 따라서 KOE205("설정하지 않은 동의 항목 포함") 는 클라이언트에서 못 막고,
      //   1) Kakao 콘솔의 동의항목에서 account_email 을 사용 가능한 상태로 만들거나
      //      ("Register as Individual" 또는 비즈앱 전환), 또는
      //   2) Kakao 동의항목에서 email 을 빼고 Supabase 의 Kakao provider 설정에서
      //      "Allow users without an email" 을 켜는
      // 두 방향 중 하나로 풀어야 한다.
      const { error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: buildOAuthRedirect(nextPath),
        },
      });
      if (error) return { ok: false, message: normalizeAuthError(error.message) };
      // 성공 시엔 즉시 외부 IdP 로 redirect 되므로 호출부 코드는 거의 실행되지 않는다.
      return { ok: true };
    },
    [],
  );

  const signOut = useCallback(async (): Promise<AuthActionResult> => {
    const { error } = await supabase.auth.signOut();
    if (error) return { ok: false, message: normalizeAuthError(error.message) };
    return { ok: true };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
      session,
      loading,
      signInWithPassword,
      signUpWithPassword,
      signInWithOAuth,
      signOut,
    }),
    [session, loading, signInWithPassword, signUpWithPassword, signInWithOAuth, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return ctx;
}

// ── 표시용 헬퍼 ───────────────────────────────────────────────────────────────
// Google/Kakao 는 user_metadata 에 name/full_name 이 있고, 이메일 가입은 email 만 있다.
export function displayNameOf(user: User | null): string {
  if (!user) return "Guest";
  const meta = user.user_metadata ?? {};
  const candidate =
    (typeof meta.full_name === "string" && meta.full_name) ||
    (typeof meta.name === "string" && meta.name) ||
    (typeof meta.user_name === "string" && meta.user_name) ||
    user.email ||
    "사용자";
  return String(candidate);
}
