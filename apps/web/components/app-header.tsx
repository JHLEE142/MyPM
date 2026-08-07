import Link from "next/link";
import { AiRouterWidget } from "@/components/ai-router-widget";
import { RefreshButton } from "@/components/refresh-button";

// 헤더 전체가 창 드래그 영역이다. 데스크톱 앱은 타이틀바를 숨기고 있어서, 이게 없으면
// 창 맨 위 얇은 띠를 정확히 잡아야만 창이 움직인다. 클릭 대상은 no-drag로 되돌린다.
export function AppHeader() {
  return (
    <header className="drag-region sticky top-0 z-40 border-b border-[#dce5e1] bg-[rgba(251,252,251,.9)] backdrop-blur-xl">
      <div className="page-shell flex h-16 items-center justify-between">
        <Link href="/projects" className="no-drag flex items-center gap-3" aria-label="MyPM 프로젝트 목록">
          <span className="grid size-9 place-items-center rounded-xl bg-[#166a58] text-sm font-black text-white shadow-sm">P</span>
          <span>
            <span className="block text-[15px] font-black tracking-[-.02em]">MyPM</span>
            <span className="block text-[10px] font-bold tracking-[.12em] text-[#72817d]">MY PROJECT MANAGER</span>
          </span>
        </Link>
        <nav className="flex items-center gap-2">
          <AiRouterWidget />
          <RefreshButton />
          <Link href="/projects" className="no-drag btn btn-ghost btn-sm">프로젝트</Link>
          <Link href="/settings" className="no-drag btn btn-ghost btn-sm">설정</Link>
          <Link href="/projects/new" className="no-drag btn btn-primary btn-sm"><span aria-hidden>＋</span> 새 프로젝트</Link>
        </nav>
      </div>
    </header>
  );
}
