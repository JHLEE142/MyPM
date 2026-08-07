import type { Metadata } from "next";
import { AppHeader } from "@/components/app-header";
import "./globals.css";

export const metadata: Metadata = {
  title: "MyPM — 마이 프로젝트 매니저",
  description: "자료에서 업무를 구조화하고 실행 가능한 일정으로 만드는 AI 프로젝트 매니저",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen">
        <AppHeader />
        {children}
      </body>
    </html>
  );
}
