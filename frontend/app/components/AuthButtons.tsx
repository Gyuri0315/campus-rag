"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { displayNameOf, useAuth } from "@/app/context/AuthContext";

const NAVY = "#25348B";
const NAVY_MUTED = "rgba(37,52,139,0.45)";

// ── 아이콘 ────────────────────────────────────────────────────────────────────
const IconUser = () => (
  <svg width="11" height="11" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="7" cy="4.5" r="2.5" />
    <path d="M2 12.5c0-2.76 2.24-5 5-5s5 2.24 5 5" />
  </svg>
);
const IconChevronDown = ({ open }: { open: boolean }) => (
  <svg
    width="9" height="9" viewBox="0 0 12 12" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    style={{ transition: "transform 0.18s ease", transform: open ? "rotate(180deg)" : "rotate(0deg)" }}
    aria-hidden
  >
    <polyline points="2,4 6,8 10,4" />
  </svg>
);
const IconLogout = () => (
  <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 1H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6" />
    <polyline points="7,4 10,7 7,10" />
    <line x1="10" y1="7" x2="4" y2="7" />
  </svg>
);

interface AuthButtonsProps {
  /** 보더/배경의 색감을 살짝 다르게 둘 수 있도록 variant 분기. 기본은 home/header 스타일. */
  variant?: "header";
}

export default function AuthButtons({ variant = "header" }: AuthButtonsProps) {
  const router = useRouter();
  const { user, loading, signOut } = useAuth();

  const [menuOpen, setMenuOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // 외부 클릭 / ESC → 메뉴 닫기
  useEffect(() => {
    if (!menuOpen) return;
    const onMouse = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onMouse);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouse);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  // 초기 세션 확인 중 — 높이 자리만 잡아 layout shift 방지.
  if (loading) {
    return (
      <div
        aria-hidden
        style={{ height: 30, width: 96, background: "transparent" }}
        className="hidden sm:block"
      />
    );
  }

  // ── 비로그인 ───────────────────────────────────────────────────────────────
  if (!user) {
    return (
      <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
        {/* Sign Up — 모바일(< sm) 에서 숨김 (기존 home 패턴 유지) */}
        <Link
          href="/auth?mode=signup"
          prefetch={false}
          className="
            hidden sm:inline-flex items-center
            px-3 sm:px-4 py-1 sm:py-1.5
            rounded-full text-xs font-medium
            transition-opacity hover:opacity-75
          "
          style={{
            color: NAVY,
            border: `1.5px solid ${NAVY}`,
            background: "transparent",
            textDecoration: "none",
          }}
        >
          Sign Up
        </Link>

        <Link
          href="/auth?mode=signin"
          prefetch={false}
          className="
            inline-flex items-center
            px-3 sm:px-5 py-1 sm:py-1.5
            rounded-full text-xs font-semibold text-white
            transition-opacity hover:opacity-80
          "
          style={{
            background: NAVY,
            textDecoration: "none",
          }}
        >
          Login
        </Link>
      </div>
    );
  }

  // ── 로그인 ─────────────────────────────────────────────────────────────────
  const name = displayNameOf(user);

  const handleSignOut = async () => {
    setMenuOpen(false);
    await signOut();
    // 세션 변경은 onAuthStateChange 가 반영하지만, 일부 서버 컴포넌트 캐시도 갱신.
    router.refresh();
  };

  return (
    <div ref={wrapperRef} className="relative flex-shrink-0" data-variant={variant}>
      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        className="
          inline-flex items-center gap-1.5 sm:gap-2
          pl-1 pr-2.5 sm:pl-1.5 sm:pr-3 py-1 sm:py-1.5
          rounded-full text-xs font-semibold
          transition-colors hover:bg-white/55
        "
        style={{
          color: NAVY,
          border: "1.5px solid rgba(37,52,139,0.25)",
          background: menuOpen ? "rgba(255,255,255,0.6)" : "rgba(255,255,255,0.35)",
          maxWidth: 200,
        }}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <span
          className="inline-flex items-center justify-center w-5 h-5 sm:w-6 sm:h-6 rounded-full text-white flex-shrink-0"
          style={{ background: NAVY }}
        >
          <IconUser />
        </span>
        <span className="truncate" style={{ maxWidth: 120 }}>
          {name}
        </span>
        <IconChevronDown open={menuOpen} />
      </button>

      {menuOpen && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1.5 z-50 rounded-xl overflow-hidden"
          style={{
            minWidth: 200,
            background: "rgba(255,255,255,0.97)",
            backdropFilter: "blur(20px)",
            WebkitBackdropFilter: "blur(20px)",
            border: "1px solid rgba(200,210,230,0.7)",
            boxShadow: "0 8px 28px rgba(0,0,0,0.13), 0 1.5px 6px rgba(0,0,0,0.07)",
          }}
        >
          {/* 계정 정보 헤더 */}
          <div
            className="px-3.5 py-2.5 flex flex-col gap-0.5"
            style={{ borderBottom: "1px solid rgba(0,0,0,0.06)" }}
          >
            <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: NAVY_MUTED }}>
              계정
            </span>
            <span className="text-xs font-semibold truncate" style={{ color: NAVY }}>
              {name}
            </span>
            {user.email && user.email !== name && (
              <span className="text-[11px] truncate" style={{ color: NAVY_MUTED }}>
                {user.email}
              </span>
            )}
          </div>

          {/* 액션 */}
          <div className="p-1.5">
            <button
              type="button"
              onClick={handleSignOut}
              className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs font-medium transition-colors hover:bg-red-50"
              style={{ color: "#c53030" }}
            >
              <IconLogout />
              <span>로그아웃</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
