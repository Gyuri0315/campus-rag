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
      signOut,
    }),
    [session, loading, signInWithPassword, signUpWithPassword, signOut],
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
