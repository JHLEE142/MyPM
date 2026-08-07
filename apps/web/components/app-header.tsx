import Link from "next/link";
import { AiRouterWidget } from "@/components/ai-router-widget";

export function AppHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-[#dce5e1] bg-[rgba(251,252,251,.9)] backdrop-blur-xl">
      <div className="page-shell flex h-16 items-center justify-between">
        <Link href="/projects" className="flex items-center gap-3" aria-label="MyPM 프로젝트 목록">
          <span className="grid size-9 place-items-center rounded-xl bg-[#166a58] text-sm font-black text-white shadow-sm">P</span>
          <span>
            <span className="block text-[15px] font-black tracking-[-.02em]">MyPM</span>
            <span className="block text-[10px] font-bold tracking-[.12em] text-[#72817d]">MY PROJECT MANAGER</span>
          </span>
        </Link>
        <nav className="flex items-center gap-2">
          <AiRouterWidget />
          <Link href="/projects" className="btn btn-ghost btn-sm">프로젝트</Link>
          <Link href="/projects/new" className="btn btn-primary btn-sm"><span aria-hidden>＋</span> 새 프로젝트</Link>
        </nav>
      </div>
    </header>
  );
}
