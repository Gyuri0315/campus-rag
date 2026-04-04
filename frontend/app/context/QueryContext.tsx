"use client";

import { createContext, useContext, useState, ReactNode } from "react";

interface QueryContextType {
  pendingQuery: string;
  setPendingQuery: (q: string) => void;
}

const QueryContext = createContext<QueryContextType>({
  pendingQuery: "",
  setPendingQuery: () => {},
});

export function QueryProvider({ children }: { children: ReactNode }) {
  const [pendingQuery, setPendingQuery] = useState("");
  return (
    <QueryContext.Provider value={{ pendingQuery, setPendingQuery }}>
      {children}
    </QueryContext.Provider>
  );
}

export const useQueryContext = () => useContext(QueryContext);
