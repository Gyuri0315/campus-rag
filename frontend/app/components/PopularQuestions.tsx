"use client";

import { useEffect, useState } from "react";
import homeData from "@/data/routes/home.json";
import { fetchPopularQuestions } from "../lib/api";

const { title, defaults } = homeData.page.popular;
const MAX_ITEMS = 5;

/**
 * 자주 묻는 질문 목록. 누르면 바로 질문한다(onSelect).
 * 여러 사용자가 물어본 질문(백엔드 /popular-questions)을 먼저, 모자라면 기본 질문으로 채운다.
 */
export default function PopularQuestions({
  onSelect,
  align = "center",
}: {
  onSelect: (question: string) => void;
  align?: "center" | "start";
}) {
  const [questions, setQuestions] = useState<string[]>(defaults.slice(0, MAX_ITEMS));

  useEffect(() => {
    const controller = new AbortController();
    fetchPopularQuestions(controller.signal).then((popular) => {
      if (controller.signal.aborted || popular.length === 0) return;
      setQuestions([...new Set([...popular, ...defaults])].slice(0, MAX_ITEMS));
    });
    return () => controller.abort();
  }, []);

  return (
    <div className={`flex flex-col gap-1.5 sm:gap-2 ${align === "center" ? "items-center" : "items-start"}`}>
      <p className="text-[11px] sm:text-xs font-semibold" style={{ color: "var(--clr-text-muted)" }}>
        {title}
      </p>
      <div className={`flex flex-wrap gap-1.5 sm:gap-2 ${align === "center" ? "justify-center" : ""}`}>
        {questions.map((question) => (
          <button
            key={question}
            onClick={() => onSelect(question)}
            className="glass-tag max-w-full px-3 py-1 sm:px-3.5 sm:py-1.5 rounded-full text-[11px] sm:text-xs font-semibold text-left"
            style={{ color: "var(--clr-navy)" }}
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}
