"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import homeData from "@/data/routes/home.json";
import { useQueryContext } from "./context/QueryContext";

const { header, hero, search, exampleTags, footer } = homeData.page;

const NAVY = "#25348B";
const NAVY_MUTED = "rgba(37,52,139,0.45)";

export default function HomePage() {
  const router = useRouter();
  const { setPendingQuery } = useQueryContext();
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSearch = () => {
    const q = query.trim();
    if (!q) return;
    setPendingQuery(q);
    router.push("/chat");
  };

  const handleTag = (tag: string) => {
    setQuery(tag);
    inputRef.current?.focus();
  };

  return (
    /* ── 그라데이션 배경 ── */
    <div className="bg-app min-h-screen flex items-center justify-center p-3 sm:p-4 lg:p-5">

      {/* ── 흰색 반투명 카드 ──
          padding 별 minHeight:
            mobile  (p-3): 12px×2 = 24px → calc(100vh - 24px)
            tablet  (p-4): 16px×2 = 32px → calc(100vh - 32px)
            desktop (p-5): 20px×2 = 40px → calc(100vh - 40px)
      ── */}
      <div
        className="
          home-card w-full flex flex-col
          rounded-2xl sm:rounded-[24px]
          min-h-[calc(100vh-24px)] sm:min-h-[calc(100vh-32px)] lg:min-h-[calc(100vh-40px)]
        "
        style={{ maxWidth: "860px" }}
      >

        {/* ── Header ── */}
        <header
          className="flex items-center justify-between px-4 py-3 sm:px-6 sm:py-4 lg:px-8 lg:py-5"
          style={{ borderBottom: "1px solid rgba(37,52,139,0.1)" }}
        >
          {/* 로고 — 좁은 화면에서 잘림 방지 */}
          <span
            className="text-sm sm:text-base font-semibold truncate min-w-0 flex-1 mr-2"
            style={{ color: NAVY }}
          >
            {header.logo}
          </span>

          <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
            {/* Sign Up — 모바일(< sm)에서 숨김 */}
            <button
              className="
                hidden sm:inline-flex
                px-3 sm:px-4 py-1 sm:py-1.5
                rounded-full text-xs font-medium
                transition-opacity hover:opacity-75
              "
              style={{ color: NAVY, border: `1.5px solid ${NAVY}`, background: "transparent" }}
            >
              {header.buttons[0]}
            </button>

            <button
              className="
                px-3 sm:px-5 py-1 sm:py-1.5
                rounded-full text-xs font-semibold text-white
                transition-opacity hover:opacity-80
              "
              style={{ background: NAVY }}
            >
              {header.buttons[1]}
            </button>
          </div>
        </header>

        {/* ── Main ── */}
        <main className="flex-1 flex flex-col items-center justify-center px-4 sm:px-8 lg:px-10 gap-6 sm:gap-8">

          {/* Hero */}
          <div className="text-center flex flex-col gap-2 sm:gap-2.5">
            <h1
              className="text-2xl sm:text-3xl lg:text-[2.35rem] font-bold leading-tight tracking-tight"
              style={{ color: NAVY }}
            >
              {hero.title}
            </h1>
            <p
              className="text-xs sm:text-sm lg:text-base"
              style={{ color: NAVY_MUTED }}
            >
              {hero.subtitle}
            </p>
          </div>

          {/* 검색창 */}
          <div className="w-full sm:max-w-[480px] lg:max-w-[520px] flex flex-col gap-2 sm:gap-3">
            <div
              className="flex items-center rounded-xl sm:rounded-2xl px-3 sm:px-4 py-1.5 bg-white shadow-sm"
              style={{ border: "1px solid rgba(37,52,139,0.1)" }}
            >
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                placeholder={search.placeholder}
                className="flex-1 min-w-0 bg-transparent outline-none text-xs sm:text-sm py-2 sm:py-2.5 font-semibold"
                style={{ color: NAVY, caretColor: NAVY }}
              />
              <button
                onClick={handleSearch}
                disabled={!query.trim()}
                className="ml-1.5 flex-shrink-0 w-8 h-8 sm:w-9 sm:h-9 rounded-full flex items-center justify-center text-white transition-opacity disabled:opacity-35"
                style={{ background: NAVY }}
                aria-label="검색"
              >
                {/* 위쪽 화살표 아이콘 */}
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="8" y1="13" x2="8" y2="3" />
                  <polyline points="3,8 8,3 13,8" />
                </svg>
              </button>
            </div>

            {/* 예시 태그 */}
            <div className="flex flex-wrap gap-1.5 sm:gap-2 justify-center">
              {exampleTags.map((tag) => (
                <button
                  key={tag}
                  onClick={() => handleTag(tag)}
                  className="glass-tag px-3 py-1 sm:px-3.5 sm:py-1.5 rounded-full text-[11px] sm:text-xs font-semibold"
                  style={{ color: NAVY }}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>
        </main>

        {/* ── Footer ── */}
        <footer
          className="py-4 sm:py-5 text-center text-[10px] sm:text-xs flex flex-col items-center gap-1.5"
          style={{ color: NAVY_MUTED }}
        >
          <span>{footer}</span>
          <a
            href="/docs"
            className="underline underline-offset-2 hover:opacity-70 transition-opacity"
            style={{ color: NAVY_MUTED }}
          >
            수집 문서 현황
          </a>
        </footer>
      </div>
    </div>
  );
}
