"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState } from "@/components/feedback";
import { Pagination, usePagination } from "@/components/pagination";
import { API_URL, errorMessage } from "@/lib/api";
import { useRefreshListener } from "@/lib/refresh";

type ReportLine = { project_id: number; project: string; percent: number | null; items: string[]; text: string };
type DailyReport = { date: string; previous_date: string; sections: Record<string, ReportLine[]>; text: string };

const SECTIONS = ["전일", "금일", "주간 목표", "월간 목표"] as const;

function isoToday(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function SectionTable({ name, lines }: { name: string; lines: ReportLine[] }) {
  const pages = usePagination(lines);
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-[#e2e9e6] px-5 py-3">
        <h3 className="text-sm font-black">{name}</h3>
        <span className="text-[11px] font-bold text-[#71807b]">프로젝트 {lines.length}개</span>
      </div>
      <ul className="divide-y divide-[#eef2f0]">
        {pages.pageItems.map((line) => (
          <li key={line.project_id} className="flex gap-3 px-5 py-3">
            <span className={`w-14 shrink-0 text-sm font-black ${line.percent === null ? "text-[#a8b5b1]" : "text-[#166a58]"}`}>
              {line.percent === null ? "—" : `${Math.round(line.percent)}%`}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-bold">{line.project}</p>
              <p className="mt-0.5 text-xs leading-5 text-[#687873]">{line.items.length ? line.items.join(" / ") : "특이사항 없음"}</p>
            </div>
          </li>
        ))}
      </ul>
      <Pagination {...pages} onChange={pages.setPage} label={name} />
    </section>
  );
}

export default function DailyReportPage() {
  const [date, setDate] = useState(isoToday());
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/reports/daily?date_=${date}`);
      if (!response.ok) throw new Error(`보고 초안을 불러오지 못했습니다 (${response.status})`);
      setReport(await response.json());
    } catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }, [date]);

  useRefreshListener(() => load(), { onWindowFocus: true });
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function copy() {
    if (!report) return;
    await navigator.clipboard.writeText(report.text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <main className="page-shell py-8 sm:py-10">
      <div className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">Daily scrum</p>
          <h1 className="mt-2 text-3xl font-black tracking-[-.04em]">데일리 스크럼 보고 초안</h1>
          <p className="mt-2 text-sm text-[#687874]">Slack #0-daily-report에 올릴 내용입니다. 그대로 복사해 붙여넣으세요.</p>
        </div>
        <div className="flex items-end gap-2">
          <label>
            <span className="label">기준 날짜</span>
            <input className="field py-2" type="date" value={date} onChange={(event) => setDate(event.target.value)} />
          </label>
          <button type="button" className="btn btn-primary" disabled={!report} onClick={() => void copy()}>
            {copied ? "복사됨" : "전체 복사"}
          </button>
        </div>
      </div>

      {error && <div className="mb-4"><ErrorState message={error} onRetry={() => void load()} /></div>}
      {loading && !report ? <LoadingState /> : report && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            {SECTIONS.map((name) => <SectionTable key={name} name={name} lines={report.sections[name] ?? []} />)}
          </div>

          <section className="panel mt-5 overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#e2e9e6] px-5 py-3">
              <h3 className="text-sm font-black">붙여넣을 원문</h3>
              <span className="text-[11px] font-bold text-[#71807b]">— 는 근거가 없어 비워 둔 칸입니다. 직접 채우세요.</span>
            </div>
            <pre className="overflow-x-auto p-5 text-xs leading-6 text-[#33423d]">{report.text}</pre>
          </section>
        </>
      )}
    </main>
  );
}
