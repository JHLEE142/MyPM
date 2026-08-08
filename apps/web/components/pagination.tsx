"use client";

import { useMemo, useState } from "react";

export const PAGE_SIZE = 10;

/**
 * 목록이 한 화면에 담기지 않을 때 잘라 보여준다.
 * 항목 수가 pageSize 이하면 페이지 UI 자체가 나오지 않는다(`visible`이 false).
 */
export function usePagination<T>(items: T[], pageSize: number = PAGE_SIZE) {
  const [page, setPage] = useState(1);
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  // 항목이 줄어 현재 페이지가 사라지면(삭제·필터) 마지막 페이지를 보여준다.
  // state를 되돌리지 않고 읽을 때만 눌러서, 항목이 다시 늘면 보던 페이지로 돌아온다.
  const start = (Math.min(page, pageCount) - 1) * pageSize;
  const pageItems = useMemo(() => items.slice(start, start + pageSize), [items, start, pageSize]);

  return {
    page: Math.min(page, pageCount),
    pageCount,
    pageItems,
    total,
    setPage,
    visible: total > pageSize,
    rangeStart: total === 0 ? 0 : start + 1,
    rangeEnd: Math.min(start + pageSize, total),
  };
}

type PaginationProps = {
  page: number;
  pageCount: number;
  total: number;
  rangeStart: number;
  rangeEnd: number;
  visible: boolean;
  onChange: (page: number) => void;
  label?: string;
};

/** 페이지 번호가 많아지면 현재 페이지 주변만 보여주고 나머지는 …으로 접는다. */
function pageNumbers(page: number, pageCount: number): (number | "gap")[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1);
  const around = [page - 1, page, page + 1].filter((value) => value > 1 && value < pageCount);
  const shown = [1, ...around, pageCount];
  const result: (number | "gap")[] = [];
  let previous = 0;
  for (const value of shown) {
    if (previous && value - previous > 1) result.push("gap");
    result.push(value);
    previous = value;
  }
  return result;
}

export function Pagination({ page, pageCount, total, rangeStart, rangeEnd, visible, onChange, label = "목록" }: PaginationProps) {
  if (!visible) return null;
  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-[#e7edea] px-4 py-3" aria-label={`${label} 페이지`}>
      <p className="text-xs font-bold text-[#71807b]">전체 {total}개 중 {rangeStart}–{rangeEnd}</p>
      <div className="flex items-center gap-1">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          aria-label="이전 페이지"
        >‹</button>
        {pageNumbers(page, pageCount).map((value, index) =>
          value === "gap" ? (
            <span key={`gap-${index}`} className="px-1 text-xs text-[#9db3ab]" aria-hidden>…</span>
          ) : (
            <button
              key={value}
              type="button"
              className={`btn btn-sm ${value === page ? "btn-primary" : "btn-ghost"}`}
              aria-current={value === page ? "page" : undefined}
              aria-label={`${value}페이지`}
              onClick={() => onChange(value)}
            >{value}</button>
          ),
        )}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={page >= pageCount}
          onClick={() => onChange(page + 1)}
          aria-label="다음 페이지"
        >›</button>
      </div>
    </nav>
  );
}
