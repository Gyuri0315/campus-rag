import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "./context/QueryContext";

const geist = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Campus RAG | 부경대학교 컴퓨터·인공지능공학부",
  description: "학과 문서와 학사 정보를 AI로 빠르게 찾아보세요.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={`${geist.variable} h-full`}>
      <body className="h-full font-[var(--font-geist-sans)] antialiased">
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
