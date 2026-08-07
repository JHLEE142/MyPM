"use client";

import { useEffect, useState } from "react";
import { hasRefreshListener, requestRefresh } from "@/lib/refresh";

/** 헤더 새로고침 버튼. ⌘R·F5로도 동작한다(데스크톱 앱에는 주소창이 없다). */
export function RefreshButton() {
  const [spinning, setSpinning] = useState(false);

  function refresh() {
    // 화면이 로더를 등록하지 않았으면(정적 화면) 전체 리로드로 넘어간다.
    if (!hasRefreshListener()) {
      window.location.reload();
      return;
    }
    setSpinning(true);
    requestRefresh();
    window.setTimeout(() => setSpinning(false), 600);
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isReloadKey = event.key === "F5" || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r");
      if (!isReloadKey || event.shiftKey) return; // ⇧⌘R은 브라우저·Electron의 강제 리로드로 넘긴다
      event.preventDefault();
      refresh();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm no-drag"
      onClick={refresh}
      aria-label="데이터 새로고침 (⌘R)"
      title="데이터 새로고침 (⌘R)"
    >
      <span aria-hidden className={`inline-block text-base leading-none${spinning ? " mypm-spin" : ""}`}>↻</span>
      <span className="hidden sm:inline">새로고침</span>
    </button>
  );
}
