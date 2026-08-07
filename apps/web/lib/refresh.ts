"use client";

import { useEffect, useRef } from "react";

/**
 * 헤더의 새로고침 버튼과 각 화면의 데이터 로더를 잇는 신호.
 *
 * 데스크톱 앱에는 주소창·새로고침 버튼이 없어서, 다른 곳(API·다른 창)에서 바뀐 데이터를
 * 다시 불러올 방법이 필요하다. 전체 페이지를 리로드하면 스크롤과 입력 중이던 값이 날아가므로,
 * 화면이 등록한 로더만 다시 실행한다. 등록한 화면이 하나도 없으면 헤더가 전체 리로드로 넘어간다.
 */
export const REFRESH_EVENT = "mypm:refresh";

let listenerCount = 0;

export function hasRefreshListener(): boolean {
  return listenerCount > 0;
}

export function requestRefresh(): void {
  window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
}

/** 화면의 데이터 로더를 새로고침 신호에 연결한다. `handler`는 매 렌더마다 새로 만들어도 된다. */
export function useRefreshListener(handler: () => void | Promise<void>, options?: { onWindowFocus?: boolean }): void {
  const latest = useRef(handler);
  const onWindowFocus = options?.onWindowFocus ?? false;

  // 렌더 중에 ref를 쓰면 concurrent 렌더에서 어긋날 수 있어 커밋 이후에 갱신한다.
  useEffect(() => {
    latest.current = handler;
  });

  useEffect(() => {
    const run = () => void latest.current();
    listenerCount += 1;
    window.addEventListener(REFRESH_EVENT, run);
    if (onWindowFocus) window.addEventListener("focus", run);
    return () => {
      listenerCount -= 1;
      window.removeEventListener(REFRESH_EVENT, run);
      if (onWindowFocus) window.removeEventListener("focus", run);
    };
  }, [onWindowFocus]);
}
