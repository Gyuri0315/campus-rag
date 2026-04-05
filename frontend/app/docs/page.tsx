"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import docsData from "@/data/routes/docs.json";

const NAVY = "#25348B";
const NAVY_MUTED = "rgba(37,52,139,0.45)";

const CATEGORIES = ["전체", "공지사항", "학부안내", "학사안내"] as const;
type Category = (typeof CATEGORIES)[number];

const IconArrowLeft = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <line x1="13" y1="8" x2="3" y2="8" />
    <polyline points="7,3 2,8 7,13" />
  </svg>
);
const IconSearch = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="6" cy="6" r="4.5" />
    <line x1="9.5" y1="9.5" x2="13" y2="13" />
  </svg>
);
const IconExternalLink = () => (
  <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M5 2H2a1 1 0 00-1 1v7a1 1 0 001 1h7a1 1 0 001-1V7" />
    <polyline points="8,1 11,1 11,4" />
    <line x1="6" y1="6" x2="11" y2="1" />
  </svg>
);
const IconDocument = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M3 1h6l3 3v9a1 1 0 01-1 1H3a1 1 0 01-1-1V2a1 1 0 011-1z" />
    <polyline points="9,1 9,4 12,4" />
    <line x1="4" y1="7" x2="10" y2="7" />
    <line x1="4" y1="9.5" x2="7.5" y2="9.5" />
  </svg>
);

function formatDate(iso: string) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const CATEGORY_COLORS: Record<string, string> = {
  공지사항: "rgba(37,52,139,0.12)",
  학부안내: "rgba(37,120,139,0.12)",
  학사안내: "rgba(120,37,139,0.1)",
};

export default function DocsPage() {
  const router = useRouter();
  const [activeCategory, setActiveCategory] = useState<Category>("전체");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    return docsData.documents.filter((doc) => {
      const matchCat = activeCategory === "전체" || doc.category === activeCategory;
      const normalize = (s: string) => s.toLowerCase().replace(/\s+/g, "");
      const matchQ = normalize(doc.title).includes(normalize(query));
      return matchCat && matchQ;
    });
  }, [activeCategory, query]);

  return (
    <div className="bg-app min-h-screen flex flex-col">
      {/* ── 헤더 ── */}
      <header
        className="flex items-center justify-between px-4 py-3 sm:px-6 sm:py-4 lg:px-8 lg:py-5 flex-shrink-0"
        style={{ borderBottom: "1px solid rgba(37,52,139,0.08)" }}
      >
        <span className="text-sm sm:text-base font-semibold" style={{ color: NAVY }}>
          부경대학교 | 컴퓨터·인공지능공학부
        </span>
      </header>

      {/* ── 본문 ── */}
      <main className="flex-1 w-full max-w-3xl mx-auto px-4 py-6 sm:px-6 sm:py-8 flex flex-col gap-6">

        {/* 뒤로가기 + 페이지 타이틀 */}
        <div className="flex flex-col gap-3">
          <button
            onClick={() => router.back()}
            className="flex items-center gap-1.5 text-sm font-medium w-fit transition-opacity hover:opacity-70"
            style={{ color: NAVY_MUTED }}
          >
            <IconArrowLeft />
            뒤로가기
          </button>

          <div className="flex flex-col gap-1">
            <h1 className="text-xl sm:text-2xl font-bold" style={{ color: NAVY }}>
              수집 문서 현황
            </h1>
            <p className="text-[11px] sm:text-xs" style={{ color: NAVY_MUTED }}>
              마지막 업데이트: {formatDate(docsData.meta.lastCrawledAt)}
              &nbsp;·&nbsp;총 {docsData.meta.totalCount}건
            </p>
          </div>
        </div>

        {/* 필터 + 검색 */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          {/* 카테고리 탭 */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className="px-3 py-1 rounded-full text-xs font-semibold transition-all"
                style={{
                  background: activeCategory === cat ? NAVY : "rgba(255,255,255,0.55)",
                  color: activeCategory === cat ? "white" : NAVY,
                  border: `1px solid ${activeCategory === cat ? NAVY : "rgba(255,255,255,0.85)"}`,
                  backdropFilter: "blur(8px)",
                }}
              >
                {cat}
              </button>
            ))}
          </div>

          {/* 검색창 */}
          <div
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm flex-1 sm:max-w-xs"
            style={{
              background: "rgba(255,255,255,0.72)",
              border: "1px solid rgba(255,255,255,0.9)",
              backdropFilter: "blur(12px)",
            }}
          >
            <span style={{ color: NAVY_MUTED }}><IconSearch /></span>
            <input
              type="text"
              placeholder="문서 제목 검색"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="flex-1 bg-transparent outline-none text-xs sm:text-sm placeholder:text-[rgba(37,52,139,0.35)]"
              style={{ color: NAVY }}
            />
          </div>
        </div>

        {/* 문서 목록 */}
        <div className="flex flex-col gap-2.5">
          {filtered.length === 0 ? (
            <div
              className="rounded-2xl p-8 text-center text-sm"
              style={{ background: "rgba(255,255,255,0.45)", color: NAVY_MUTED }}
            >
              검색 결과가 없습니다.
            </div>
          ) : (
            filtered.map((doc) => (
              <div
                key={doc.id}
                className="rounded-2xl px-4 py-3.5 sm:px-5 sm:py-4 flex items-center gap-3 sm:gap-4 transition-all hover:shadow-md"
                style={{
                  background: "rgba(255,255,255,0.58)",
                  backdropFilter: "blur(16px)",
                  WebkitBackdropFilter: "blur(16px)",
                  border: "1px solid rgba(255,255,255,0.88)",
                }}
              >
                {/* 아이콘 */}
                <div
                  className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center"
                  style={{ background: "rgba(37,52,139,0.08)", color: NAVY }}
                >
                  <IconDocument />
                </div>

                {/* 정보 */}
                <div className="flex-1 min-w-0 flex flex-col gap-1">
                  <span className="text-xs sm:text-sm font-semibold leading-snug truncate" style={{ color: NAVY }}>
                    {doc.title}
                  </span>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className="rounded-full px-2 py-0.5 text-[9px] sm:text-[10px] font-semibold"
                      style={{
                        background: CATEGORY_COLORS[doc.category] ?? "rgba(37,52,139,0.1)",
                        color: NAVY,
                      }}
                    >
                      {doc.category}
                    </span>
                    {doc.date && (
                      <span className="text-[10px] sm:text-[11px]" style={{ color: NAVY_MUTED }}>
                        {doc.date}
                      </span>
                    )}
                  </div>
                </div>

                {/* 원문 보기 */}
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex-shrink-0 flex items-center gap-1 text-[11px] sm:text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors hover:bg-white/80"
                  style={{
                    color: NAVY,
                    background: "rgba(255,255,255,0.55)",
                    border: "1px solid rgba(255,255,255,0.85)",
                  }}
                >
                  <span className="hidden sm:inline">원문 보기</span>
                  <IconExternalLink />
                </a>
              </div>
            ))
          )}
        </div>
      </main>

      {/* ── 푸터 ── */}
      <footer className="py-4 text-center text-[10px] sm:text-xs flex-shrink-0" style={{ color: NAVY_MUTED }}>
        공식 문서 기반 답변 · 첨부 파일 · 최신 공지 반영
      </footer>
    </div>
  );
}
