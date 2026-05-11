import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "./context/QueryContext";
import { AuthProvider } from "./context/AuthContext";

export const metadata: Metadata = {
  title: "unira",
  description: "학과 문서와 학사 정보를 AI로 빠르게 찾아보세요.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className="h-full">
      <body className="h-full antialiased" style={{ fontFamily: "'LaundryGothic', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif" }}>
        <AuthProvider>
          <QueryProvider>{children}</QueryProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
