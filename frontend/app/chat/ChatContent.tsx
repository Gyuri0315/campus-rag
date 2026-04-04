"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import chatData from "@/data/routes/chat.json";
import { useQueryContext } from "@/app/context/QueryContext";

// ── 타입 ─────────────────────────────────────────────────────────────────────
interface Attachment {
  name: string;
  url: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[];
}

interface HistoryItem {
  id: string;
  title: string;
}

type ChatState = "idle" | "loading" | "success" | "error";

// ── 고유 ID ───────────────────────────────────────────────────────────────────
let _seq = 0;
const uid = () => `${Date.now()}-${++_seq}`;

// ── 더미 응답 ─────────────────────────────────────────────────────────────────
const DUMMY_REPLY = chatData.chat.messages[1] as {
  role: "assistant";
  content: string;
  attachments: Attachment[];
};

const { sidebar, chat: chatMeta } = chatData;

// ── 아이콘 ────────────────────────────────────────────────────────────────────
const IconSidebar = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="2" y="2" width="14" height="14" rx="2" />
    <line x1="7" y1="2" x2="7" y2="16" />
  </svg>
);
const IconPlus = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="7" y1="1" x2="7" y2="13" />
    <line x1="1" y1="7" x2="13" y2="7" />
  </svg>
);
const IconSearch = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="6" cy="6" r="4.5" />
    <line x1="9.5" y1="9.5" x2="13" y2="13" />
  </svg>
);
const IconSend = () => (
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="8" y1="14" x2="8" y2="3" />
    <polyline points="3,8 8,3 13,8" />
  </svg>
);
const IconStar = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
    <polygon points="7,1 8.8,5.2 13.5,5.6 10.1,8.7 11.1,13.3 7,10.9 2.9,13.3 3.9,8.7 0.5,5.6 5.2,5.2" />
  </svg>
);
const IconPencil = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M9.5 2.5 L11.5 4.5 L4.5 11.5 L2 12 L2.5 9.5 Z" />
    <line x1="8" y1="4" x2="10" y2="6" />
  </svg>
);
const IconTrash = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6">
    <polyline points="1,3 3,3 13,3" />
    <path d="M4 3V2a1 1 0 011-1h4a1 1 0 011 1v1" />
    <rect x="2.5" y="4" width="9" height="8.5" rx="1" />
    <line x1="5.5" y1="6.5" x2="5.5" y2="10" />
    <line x1="8.5" y1="6.5" x2="8.5" y2="10" />
  </svg>
);
const IconAttach = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8">
    <line x1="8" y1="3" x2="8" y2="13" />
    <line x1="3" y1="8" x2="13" y2="8" />
  </svg>
);
const IconDownload = () => (
  <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8">
    <line x1="6" y1="1" x2="6" y2="8" />
    <polyline points="3,5.5 6,8 9,5.5" />
    <line x1="2" y1="11" x2="10" y2="11" />
  </svg>
);

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────
export default function ChatContent() {
  const router = useRouter();
  const { pendingQuery, setPendingQuery } = useQueryContext();

  const [history, setHistory] = useState<HistoryItem[]>(sidebar.history);
  const [activeId, setActiveId] = useState<string>("chat-001");
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [chatState, setChatState] = useState<ChatState>("idle");

  // ── 사이드바 상태 (SSR-safe: false로 시작, mount 후 보정) ──
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // isMobile: < 768px → 사이드바를 fixed 오버레이로 표시
  const [isMobile, setIsMobile] = useState(false);

  const [contextMenuId, setContextMenuId] = useState<string | null>(null);
  const [chatTitle, setChatTitle] = useState("졸업요건 질문");
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);
  // AI 응답 타이머 — 중복 실행 방지용
  const aiTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── 반응형: mount 시 viewport 크기 감지 ────────────────────────
  useEffect(() => {
    const update = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
      // 768 미만: 항상 닫힘 / 1024 이상: 기본 열림
      if (mobile) {
        setSidebarOpen(false);
      } else {
        // 처음 열릴 때만 desktop 기본값 적용
        setSidebarOpen(window.innerWidth >= 1024);
      }
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── 최초 진입: Context에서 질문을 꺼내 대화 시작 ──────────────
  // 소비 후 context를 비워 재방문 시 중복 실행을 막는다.
  // cleanup으로 타이머를 취소해 StrictMode 이중 실행도 방지한다.
  useEffect(() => {
    if (pendingQuery) {
      startConversation(pendingQuery);
      setPendingQuery("");
    }
    return () => {
      if (aiTimerRef.current) clearTimeout(aiTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 외부 클릭 → 컨텍스트 메뉴 닫기 ───────────────────────────
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenuId(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // ── 자동 스크롤 ───────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, chatState]);

  // ── AI 응답 예약 (공통) ───────────────────────────────────────
  // 이전에 예약된 타이머가 있으면 취소해 중복 AI 메시지를 방지한다.
  const scheduleAIReply = () => {
    if (aiTimerRef.current) clearTimeout(aiTimerRef.current);
    setChatState("loading");
    aiTimerRef.current = setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        { id: uid(), role: "assistant", content: DUMMY_REPLY.content, attachments: DUMMY_REPLY.attachments },
      ]);
      setChatState("success");
      aiTimerRef.current = null;
    }, 1600);
  };

  // ── 대화 시작 ─────────────────────────────────────────────────
  const startConversation = (q: string) => {
    setMessages([{ id: uid(), role: "user", content: q }]);
    scheduleAIReply();
  };

  // ── SEND_MESSAGE ──────────────────────────────────────────────
  const handleSend = () => {
    const q = inputValue.trim();
    if (!q || chatState === "loading") return;
    setInputValue("");
    setMessages((prev) => [...prev, { id: uid(), role: "user", content: q }]);
    scheduleAIReply();
  };

  // ── NEW_CHAT ──────────────────────────────────────────────────
  const handleNewChat = () => {
    setMessages([]);
    setChatState("idle");
    setInputValue("");
    setActiveId("");
    setChatTitle("새 채팅");
    setContextMenuId(null);
    if (isMobile) setSidebarOpen(false);
    router.replace("/chat");
  };

  // ── LOAD_HISTORY ──────────────────────────────────────────────
  const handleLoadHistory = (item: HistoryItem) => {
    setActiveId(item.id);
    setChatTitle(item.title);
    setContextMenuId(null);
    if (isMobile) setSidebarOpen(false); // 모바일: 선택 후 사이드바 닫기
    const q =
      item.id === "chat-001" ? "졸업 요건을 알려주세요."
      : item.id === "chat-002" ? "컴퓨터공학과 교수진 목록을 알려주세요."
      : "캡스톤 디자인 과목에 대해 알려주세요.";
    startConversation(q);
  };

  // ── DELETE ────────────────────────────────────────────────────
  const handleDelete = (id: string) => {
    setHistory((prev) => prev.filter((h) => h.id !== id));
    setContextMenuId(null);
    if (activeId === id) handleNewChat();
  };

  // ── RENAME ────────────────────────────────────────────────────
  const handleRenameStart = (item: HistoryItem) => {
    setRenameId(item.id);
    setRenameValue(item.title);
    setContextMenuId(null);
  };
  const handleRenameConfirm = (id: string) => {
    if (renameValue.trim()) {
      setHistory((prev) => prev.map((h) => (h.id === id ? { ...h, title: renameValue.trim() } : h)));
      if (activeId === id) setChatTitle(renameValue.trim());
    }
    setRenameId(null);
  };

  // ── 사이드바 너비 ─────────────────────────────────────────────
  const sidebarWidth = sidebarOpen ? (isMobile ? "min(280px, 80vw)" : "210px") : "0px";

  return (
    <div className="bg-app flex h-screen overflow-hidden relative">

      {/* ── 모바일 백드롭 (사이드바 오버레이 시 배경 어둡게) ── */}
      {isMobile && sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/20 backdrop-blur-[1px] transition-opacity"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── 사이드바 ─────────────────────────────────────────── */}
      <aside
        className="flex flex-col flex-shrink-0 transition-all duration-300 overflow-hidden"
        style={{
          // 모바일: fixed overlay / 데스크톱: 일반 flow
          position: isMobile ? "fixed" : "relative",
          top: isMobile ? 0 : undefined,
          left: isMobile ? 0 : undefined,
          bottom: isMobile ? 0 : undefined,
          zIndex: isMobile ? 40 : undefined,
          width: sidebarWidth,
          background: "rgba(255,255,255,0.48)",
          backdropFilter: "blur(18px)",
          WebkitBackdropFilter: "blur(18px)",
          borderRight: "1px solid rgba(255,255,255,0.7)",
        }}
      >
        {sidebarOpen && (
          <div
            className="flex flex-col h-full px-3 py-4 gap-1 overflow-hidden"
            style={{ minWidth: isMobile ? "min(280px, 80vw)" : "210px" }}
          >
            {/* 새 채팅 */}
            <button
              onClick={handleNewChat}
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium transition-colors hover:bg-white/50"
              style={{ color: "var(--clr-text)" }}
            >
              <IconPlus />
              새 채팅
            </button>

            {/* 검색 */}
            <button
              className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition-colors hover:bg-white/50"
              style={{ color: "var(--clr-text-muted)" }}
            >
              <IconSearch />
              검색
            </button>

            {/* 최근 항목 */}
            <p
              className="px-3 pt-3 pb-1 text-xs font-semibold uppercase tracking-wide"
              style={{ color: "var(--clr-text-muted)" }}
            >
              {sidebar.recentLabel}
            </p>

            {/* 히스토리 목록 */}
            <div className="flex flex-col gap-0.5 flex-1 overflow-y-auto">
              {history.map((item) => (
                <div key={item.id} className="relative group">
                  {renameId === item.id ? (
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={() => handleRenameConfirm(item.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleRenameConfirm(item.id);
                        if (e.key === "Escape") setRenameId(null);
                      }}
                      className="w-full px-3 py-2 text-sm rounded-xl outline-none"
                      style={{
                        background: "rgba(255,255,255,0.7)",
                        color: "var(--clr-text)",
                        border: "1px solid rgba(255,255,255,0.9)",
                      }}
                    />
                  ) : (
                    <button
                      onClick={() => handleLoadHistory(item)}
                      className="w-full flex items-center justify-between px-3 py-2 rounded-xl text-sm text-left transition-colors hover:bg-white/50"
                      style={{
                        background: activeId === item.id ? "rgba(255,255,255,0.6)" : "transparent",
                        color: "var(--clr-text)",
                        fontWeight: activeId === item.id ? 600 : 400,
                      }}
                    >
                      <span className="truncate flex-1">{item.title}</span>

                      {/* ··· 버튼 */}
                      <span
                        className="opacity-0 group-hover:opacity-100 transition-opacity ml-1 px-1 rounded hover:bg-white/60 text-base leading-none"
                        style={{ color: "var(--clr-text-muted)" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setContextMenuId(contextMenuId === item.id ? null : item.id);
                        }}
                      >
                        ···
                      </span>
                    </button>
                  )}

                  {/* 컨텍스트 메뉴
                      모바일: 항목 아래(top-full left-0)
                      데스크톱: 항목 오른쪽(left-full top-0)
                  */}
                  {contextMenuId === item.id && (
                    <div
                      ref={contextMenuRef}
                      className="absolute z-50 rounded-xl shadow-lg overflow-hidden"
                      style={{
                        top: isMobile ? "100%" : 0,
                        left: isMobile ? 0 : "100%",
                        marginTop: isMobile ? "4px" : 0,
                        marginLeft: isMobile ? 0 : "4px",
                        background: "rgba(255,255,255,0.95)",
                        backdropFilter: "blur(16px)",
                        WebkitBackdropFilter: "blur(16px)",
                        border: "1px solid rgba(255,255,255,0.9)",
                        minWidth: "130px",
                      }}
                    >
                      <button
                        onClick={() => setContextMenuId(null)}
                        className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm hover:bg-white/60 transition-colors"
                        style={{ color: "var(--clr-text)" }}
                      >
                        <IconStar /> 즐겨찾기
                      </button>
                      <button
                        onClick={() => handleRenameStart(item)}
                        className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm hover:bg-white/60 transition-colors"
                        style={{ color: "var(--clr-text)" }}
                      >
                        <IconPencil /> 이름 변경
                      </button>
                      <button
                        onClick={() => handleDelete(item.id)}
                        className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm hover:bg-red-50 transition-colors"
                        style={{ color: "#c0392b" }}
                      >
                        <IconTrash /> 삭제
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </aside>

      {/* ── 채팅 영역 ────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">

        {/* 헤더 */}
        <div
          className="flex items-center gap-2.5 sm:gap-3 px-3 py-2.5 sm:px-5 sm:py-3 flex-shrink-0"
          style={{ borderBottom: "1px solid rgba(255,255,255,0.5)" }}
        >
          {/* 사이드바 토글 — 항상 표시 (모바일: 열기 버튼) */}
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="p-1.5 rounded-lg hover:bg-white/40 transition-colors flex-shrink-0"
            style={{ color: "var(--clr-text-muted)" }}
          >
            <IconSidebar />
          </button>

          <span
            className="text-xs sm:text-sm font-semibold truncate"
            style={{ color: "var(--clr-text)" }}
          >
            {chatTitle}
          </span>
        </div>

        {/* 메시지 영역 */}
        <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-5 sm:py-6 flex flex-col gap-4 sm:gap-5">

          {/* 빈 상태 */}
          {messages.length === 0 && chatState === "idle" && (
            <div className="flex-1 flex items-center justify-center px-4 text-center">
              <p className="text-xs sm:text-sm" style={{ color: "var(--clr-text-muted)" }}>
                궁금한 내용을 아래 입력창에 입력해 보세요.
              </p>
            </div>
          )}

          {messages.map((msg) =>
            msg.role === "user" ? (
              /* ── 사용자 말풍선 ── */
              <div key={msg.id} className="flex justify-end">
                <span
                  className="max-w-[80%] sm:max-w-xs md:max-w-md px-3 sm:px-4 py-2 sm:py-2.5 rounded-2xl text-xs sm:text-sm leading-relaxed"
                  style={{
                    background: "rgba(255,255,255,0.55)",
                    color: "var(--clr-text)",
                    border: "1px solid rgba(255,255,255,0.75)",
                    backdropFilter: "blur(10px)",
                    WebkitBackdropFilter: "blur(10px)",
                  }}
                >
                  {msg.content}
                </span>
              </div>
            ) : (
              /* ── AI 답변 카드 ── */
              <div key={msg.id} className="flex justify-start">
                <div
                  className="glass-card rounded-2xl p-4 sm:p-5 max-w-[88%] sm:max-w-lg shadow-sm flex flex-col gap-3 sm:gap-4"
                >
                  <p className="text-xs sm:text-sm leading-relaxed" style={{ color: "var(--clr-text)" }}>
                    {msg.content}
                  </p>

                  {/* 첨부파일 */}
                  {msg.attachments && msg.attachments.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 sm:gap-2">
                      {msg.attachments.map((att) => (
                        <a
                          key={att.name}
                          href={att.url}
                          className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full text-[11px] sm:text-xs font-medium transition-colors hover:bg-white/80"
                          style={{
                            background: "rgba(255,255,255,0.55)",
                            border: "1px solid rgba(255,255,255,0.85)",
                            color: "var(--clr-navy)",
                          }}
                        >
                          {att.name}
                          <IconDownload />
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          )}

          {/* 로딩 */}
          {chatState === "loading" && (
            <div className="flex justify-start">
              <div className="glass-card rounded-2xl px-4 sm:px-5 py-3 sm:py-4 shadow-sm">
                <div className="flex items-center gap-2">
                  <div className="flex gap-1.5">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                  </div>
                  <span className="text-[11px] sm:text-xs" style={{ color: "var(--clr-text-muted)" }}>
                    답변을 생성하고 있어요...
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* 에러 */}
          {chatState === "error" && (
            <div className="flex justify-start">
              <div className="glass-card rounded-2xl px-4 sm:px-5 py-3 sm:py-4 shadow-sm flex flex-wrap items-center gap-2 sm:gap-3">
                <p className="text-xs sm:text-sm" style={{ color: "#c0392b" }}>
                  답변 생성에 실패했어요.
                </p>
                <button
                  onClick={() => setChatState("idle")}
                  className="text-xs px-3 py-1.5 rounded-full border font-medium"
                  style={{ color: "var(--clr-navy)", borderColor: "var(--clr-navy)" }}
                >
                  다시 시도하기
                </button>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* ── 입력창 + 면책 문구 ──────────────────────────────── */}
        <div className="flex-shrink-0 px-3 pb-3 sm:px-5 sm:pb-4 flex flex-col gap-1.5 sm:gap-2">
          <div className="glass-input flex items-center rounded-xl sm:rounded-2xl px-3 sm:px-4 py-1.5 sm:py-2 shadow-sm gap-1.5 sm:gap-2">
            {/* 첨부 버튼 */}
            <button
              className="flex-shrink-0 p-1 sm:p-1.5 rounded-lg hover:bg-white/50 transition-colors"
              style={{ color: "var(--clr-text-muted)" }}
            >
              <IconAttach />
            </button>

            {/* 텍스트 입력 */}
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
              placeholder=""
              disabled={chatState === "loading"}
              className="flex-1 min-w-0 bg-transparent outline-none text-xs sm:text-sm py-1.5 sm:py-2 font-semibold placeholder:text-gray-400 disabled:opacity-60"
              style={{ color: "var(--clr-text)" }}
            />

            {/* 전송 버튼 */}
            <button
              onClick={handleSend}
              disabled={!inputValue.trim() || chatState === "loading"}
              className="flex-shrink-0 w-7 h-7 sm:w-8 sm:h-8 rounded-full flex items-center justify-center text-white transition-opacity disabled:opacity-35"
              style={{ background: "var(--clr-navy)" }}
            >
              <IconSend />
            </button>
          </div>

          {/* 면책 문구 */}
          <p
            className="text-center text-[10px] sm:text-xs leading-relaxed"
            style={{ color: "var(--clr-text-muted)" }}
          >
            {chatMeta.disclaimer}
          </p>
        </div>
      </div>
    </div>
  );
}
