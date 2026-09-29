"use client";

import { createContext, useContext, useCallback, useRef, ReactNode } from "react";

interface QueryContextType {
  setPendingQuery: (q: string) => void;
  /**
   * 랜딩 페이지가 넘긴 질문을 "읽고 즉시 비우는" 원자적 연산으로 노출.
   * 이전엔 pendingQuery(state)를 그대로 노출해서, /chat 페이지가 두 번 마운트되면
   * (예: page.tsx의 로그인 자동 리다이렉트 useEffect와 handleSearch()의 router.push가
   * 동시에 /chat 이동을 트리거하는 경우) 두 인스턴스가 각자 별도의 ChatContent
   * 컴포넌트로 렌더되어 서로 다른 pendingChatCreateRef를 가지므로, clear가
   * 반영되기 전에 둘 다 같은 pendingQuery 값을 읽어 채팅 세션을 두 번 생성하는
   * 레이스가 발생했다. ref 기반으로 바꾸면 같은 값을 두 번 가져갈 수 없다(먼저
   * 호출한 쪽만 실제 값을 받고, 그 다음 호출은 항상 "").
   */
  takePendingQuery: () => string;
}

const QueryContext = createContext<QueryContextType>({
  setPendingQuery: () => {},
  takePendingQuery: () => "",
});

export function QueryProvider({ children }: { children: ReactNode }) {
  const pendingQueryRef = useRef("");

  const setPendingQuery = useCallback((q: string) => {
    pendingQueryRef.current = q;
  }, []);

  const takePendingQuery = useCallback(() => {
    const q = pendingQueryRef.current;
    pendingQueryRef.current = "";
    return q;
  }, []);

  return (
    <QueryContext.Provider value={{ setPendingQuery, takePendingQuery }}>
      {children}
    </QueryContext.Provider>
  );
}

export const useQueryContext = () => useContext(QueryContext);
