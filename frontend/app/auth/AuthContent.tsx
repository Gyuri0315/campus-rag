"use client";

import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import Link from "next/link";

import { useAuth } from "@/app/context/AuthContext";

const NAVY = "#25348B";
const NAVY_MUTED = "rgba(37,52,139,0.45)";

// 실제 강제는 auth.users 트리거(enforce_school_email_domain, 010 + 013
// 마이그레이션)에서 하고, 이건 사용자에게 즉시 피드백을 주기 위한 클라이언트
// 측 사전 검증이다. office.pknu.ac.kr는 행정실 등 교직원 계정으로 추정.
const SCHOOL_EMAIL_RE = /@(pknu\.ac\.kr|pukyong\.ac\.kr|office\.pknu\.ac\.kr)$/i;

// ── 아이콘 ────────────────────────────────────────────────────────────────────
const IconArrowLeft = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <line x1="13" y1="8" x2="3" y2="8" />
    <polyline points="7,3 2,8 7,13" />
  </svg>
);

const IconSpinner = () => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 16 16"
    fill="none"
    className="animate-spin"
    aria-hidden
  >
    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
    <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

// lucide Eye / EyeOff 와 같은 모양 (앱은 아이콘 라이브러리 없이 인라인 SVG를 쓴다)
const IconEye = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const IconEyeOff = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" />
    <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" />
    <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" />
    <path d="m2 2 20 20" />
  </svg>
);

const INPUT_BORDER = "rgba(37,52,139,0.12)";
const INPUT_BORDER_FOCUS = "rgba(37,52,139,0.4)";
const INPUT_BORDER_ERROR = "rgba(197,48,48,0.55)";
const ERROR_RED = "#c53030";

// 비밀번호 입력 + 우측 "보기/숨기기" 토글 (로그인·회원가입 공통)
function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  placeholder,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  placeholder: string;
  error?: string | null;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const [visible, setVisible] = useState(false);
  const idleBorder = error ? INPUT_BORDER_ERROR : INPUT_BORDER;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[11px] font-semibold" style={{ color: NAVY_MUTED }}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className="w-full rounded-xl py-2.5 pl-3.5 pr-10 text-xs sm:text-sm outline-none transition-colors placeholder:text-gray-400"
          style={{
            background: "rgba(255,255,255,0.85)",
            border: `1.5px solid ${idleBorder}`,
            color: NAVY,
            caretColor: NAVY,
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = error ? INPUT_BORDER_ERROR : INPUT_BORDER_FOCUS; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = idleBorder; }}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "비밀번호 숨기기" : "비밀번호 표시"}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-xl transition-opacity hover:opacity-70"
          style={{ color: NAVY_MUTED }}
        >
          {visible ? <IconEyeOff /> : <IconEye />}
        </button>
      </div>
      {error && (
        <span id={errorId} role="alert" className="text-[11px] font-medium" style={{ color: ERROR_RED }}>
          {error}
        </span>
      )}
    </div>
  );
}

type Mode = "signin" | "signup";

export default function AuthContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { signInWithPassword, signUpWithPassword, user, loading } = useAuth();

  // ── 모드(?mode=signup|signin), 복귀 경로(?next=/foo) ─────────────────────────
  const initialMode: Mode = searchParams?.get("mode") === "signup" ? "signup" : "signin";
  const [mode, setMode] = useState<Mode>(initialMode);
  const nextPath = useMemo(() => {
    const raw = searchParams?.get("next") ?? "/";
    return raw.startsWith("/") ? raw : "/";
  }, [searchParams]);

  // ── 폼 상태 ────────────────────────────────────────────────────────────────
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // 콜백 라우트가 에러로 redirect 시킨 경우 ?error=... 표시
  useEffect(() => {
    const err = searchParams?.get("error");
    if (err) setError(err);
  }, [searchParams]);

  // 이미 로그인된 상태로 /auth 진입 → next 로 보내기
  useEffect(() => {
    if (!loading && user) {
      router.replace(nextPath);
    }
  }, [user, loading, nextPath, router]);

  // 모드 전환 시 에러/안내 메시지 초기화
  const switchMode = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    setError(null);
    setInfo(null);
    setPasswordConfirm("");
    setConfirmError(null);
  };

  // ── 이메일 폼 제출 ─────────────────────────────────────────────────────────
  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setInfo(null);
    setConfirmError(null);

    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setError("이메일과 비밀번호를 모두 입력해 주세요.");
      return;
    }
    if (mode === "signup" && password.length < 6) {
      setError("비밀번호는 6자 이상이어야 합니다.");
      return;
    }
    if (mode === "signup" && password !== passwordConfirm) {
      setConfirmError("비밀번호가 일치하지 않습니다.");
      return;
    }
    if (mode === "signup" && !SCHOOL_EMAIL_RE.test(trimmedEmail)) {
      setError(
        "학교 이메일(@pknu.ac.kr, @pukyong.ac.kr, @office.pknu.ac.kr)로만 가입할 수 있습니다.",
      );
      return;
    }

    setSubmitting(true);
    try {
      const result =
        mode === "signin"
          ? await signInWithPassword(trimmedEmail, password)
          : await signUpWithPassword(trimmedEmail, password);

      if (!result.ok) {
        setError(result.message ?? "요청이 실패했습니다.");
        return;
      }

      if (mode === "signup" && result.emailConfirmationRequired) {
        setInfo(result.message ?? "확인 메일을 보냈습니다. 메일함을 확인해 주세요.");
        return;
      }

      router.replace(nextPath);
    } finally {
      setSubmitting(false);
    }
  };

  // ── 렌더 ──────────────────────────────────────────────────────────────────
  const isSignup = mode === "signup";

  return (
    <div className="bg-app min-h-[100dvh] flex items-center justify-center p-3 sm:p-4 lg:p-5">
      <div
        className="
          home-card w-full flex flex-col
          rounded-2xl sm:rounded-[24px]
        "
        style={{ maxWidth: "440px" }}
      >
        {/* ── Header ── */}
        <header
          className="flex items-center justify-between px-5 py-3 sm:px-6 sm:py-4"
          style={{ borderBottom: "1px solid rgba(37,52,139,0.1)" }}
        >
          <Link
            href="/"
            prefetch={false}
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg transition-colors hover:bg-white/40"
            style={{ color: NAVY_MUTED, textDecoration: "none" }}
            aria-label="홈으로 돌아가기"
          >
            <IconArrowLeft />
            <span className="text-xs font-medium">홈</span>
          </Link>

          <div className="flex items-center gap-2 min-w-0">
            <Image
              src="/pukyong_logo.png"
              alt="부경대학교 로고"
              width={26}
              height={26}
              className="flex-shrink-0 object-contain"
              style={{ width: 26, height: 26 }}
            />
            <span className="text-xs sm:text-sm font-semibold truncate" style={{ color: NAVY }}>
              부경대학교
            </span>
          </div>
        </header>

        {/* ── Body ── */}
        <main className="flex flex-col gap-5 px-6 py-7 sm:px-8 sm:py-8">
          {/* 모드 탭 */}
          <div
            className="flex p-1 rounded-xl"
            style={{ background: "rgba(37,52,139,0.06)", border: "1px solid rgba(37,52,139,0.08)" }}
          >
            {(["signin", "signup"] as const).map((m) => {
              const active = m === mode;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => switchMode(m)}
                  className="flex-1 py-1.5 rounded-lg text-xs sm:text-[13px] font-semibold transition-all"
                  style={{
                    background: active ? "white" : "transparent",
                    color: active ? NAVY : NAVY_MUTED,
                    boxShadow: active ? "0 1px 4px rgba(37,52,139,0.10)" : "none",
                  }}
                >
                  {m === "signin" ? "로그인" : "회원가입"}
                </button>
              );
            })}
          </div>

          {/* 이메일 / 비밀번호 폼 (학교 이메일만 가입 가능) */}
          <form className="flex flex-col gap-3" onSubmit={handleSubmit} noValidate>
            <label className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold" style={{ color: NAVY_MUTED }}>
                이메일
              </span>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="학번@pknu.ac.kr"
                className="rounded-xl px-3.5 py-2.5 text-xs sm:text-sm outline-none transition-colors placeholder:text-gray-400"
                style={{
                  background: "rgba(255,255,255,0.85)",
                  border: "1.5px solid rgba(37,52,139,0.12)",
                  color: NAVY,
                  caretColor: NAVY,
                }}
                onFocus={(e) => { e.currentTarget.style.borderColor = "rgba(37,52,139,0.4)"; }}
                onBlur={(e) => { e.currentTarget.style.borderColor = "rgba(37,52,139,0.12)"; }}
              />
              {isSignup && (
                <span className="text-[11px]" style={{ color: NAVY_MUTED }}>
                  @pknu.ac.kr, @pukyong.ac.kr, @office.pknu.ac.kr 학교 이메일만 가입할 수 있어요
                </span>
              )}
            </label>

            <PasswordField
              label="비밀번호"
              value={password}
              onChange={(value) => { setPassword(value); setConfirmError(null); }}
              autoComplete={isSignup ? "new-password" : "current-password"}
              placeholder={isSignup ? "6자 이상" : "비밀번호"}
            />

            {isSignup && (
              <PasswordField
                label="비밀번호 확인"
                value={passwordConfirm}
                onChange={(value) => { setPasswordConfirm(value); setConfirmError(null); }}
                autoComplete="new-password"
                placeholder="비밀번호를 한 번 더 입력"
                error={confirmError}
              />
            )}

            {/* 에러 / 안내 메시지 */}
            {error && (
              <p
                className="text-xs leading-relaxed px-3 py-2 rounded-lg"
                style={{
                  background: "rgba(229,62,62,0.08)",
                  color: "#c53030",
                  border: "1px solid rgba(229,62,62,0.18)",
                }}
              >
                {error}
              </p>
            )}
            {info && (
              <p
                className="text-xs leading-relaxed px-3 py-2 rounded-lg"
                style={{
                  background: "rgba(37,52,139,0.07)",
                  color: NAVY,
                  border: "1px solid rgba(37,52,139,0.18)",
                }}
              >
                {info}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="flex items-center justify-center gap-2 mt-1 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-55 disabled:cursor-not-allowed"
              style={{ background: NAVY }}
            >
              {submitting && <IconSpinner />}
              <span>{isSignup ? "회원가입" : "로그인"}</span>
            </button>
          </form>

          {/* 모드 전환 텍스트 */}
          <p className="text-xs text-center" style={{ color: NAVY_MUTED }}>
            {isSignup ? "이미 계정이 있으세요?" : "계정이 없으세요?"}{" "}
            <button
              type="button"
              onClick={() => switchMode(isSignup ? "signin" : "signup")}
              className="font-semibold underline underline-offset-2 hover:opacity-75 transition-opacity"
              style={{ color: NAVY, background: "transparent" }}
            >
              {isSignup ? "로그인" : "회원가입"}
            </button>
          </p>
        </main>

        {/* ── Footer ── */}
        <footer
          className="py-3 text-center text-[10px] sm:text-[11px]"
          style={{
            color: NAVY_MUTED,
            borderTop: "1px solid rgba(37,52,139,0.08)",
          }}
        >
          국립부경대학교 재학생만 이용할 수 있습니다
        </footer>
      </div>
    </div>
  );
}
