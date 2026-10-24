import type { Metadata, Viewport } from "next";
import "./globals.css";
import { QueryProvider } from "./context/QueryContext";
import { AuthProvider } from "./context/AuthContext";

export const metadata: Metadata = {
  title: "unira",
  description: "학과 문서와 학사 정보를 AI로 빠르게 찾아보세요.",
};

// 모바일 상단 상태표시줄/주소창 색을 앱 배경(.bg-app 그라데이션 시작색)과 맞춘다.
// globals.css의 --clr-app-bg와 같은 값이어야 한다.
export const viewport: Viewport = {
  themeColor: "#c6daf0",
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
