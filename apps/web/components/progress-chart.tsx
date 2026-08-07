"use client";

import { useMemo, useState } from "react";
import type { Placement, ScheduleSnapshot, Task } from "@mypm/shared-types";

type View = "daily" | "weekly" | "monthly";
type TaskHours = { task: Task | undefined; hours: number };
type ChartNode = {
  key: string;
  axisLabel: string;
  periodLabel: string;
  assigned: number;
  completed: number;
  percent: number;
  current: boolean;
  tasks: TaskHours[];
};

const viewLabels: Record<View, string> = { daily: "일간", weekly: "주간", monthly: "월간" };

function parseDate(value: string): Date {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

function dateKey(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function mondayKey(value: Date): string {
  const monday = new Date(value);
  monday.setDate(value.getDate() - ((value.getDay() + 6) % 7));
  return dateKey(monday);
}

function groupKey(date: string, view: View): string {
  if (view === "daily") return date;
  if (view === "weekly") return mondayKey(parseDate(date));
  return date.slice(0, 7);
}

function periodLabels(key: string, view: View): { axis: string; full: string } {
  if (view === "monthly") {
    const [year, month] = key.split("-").map(Number);
    return { axis: `${month}월`, full: `${year}년 ${month}월` };
  }
  const value = parseDate(key);
  if (view === "weekly") return { axis: `${value.getMonth() + 1}/${value.getDate()} 주`, full: `${value.getMonth() + 1}월 ${value.getDate()}일 주` };
  const full = new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" }).format(value);
  return { axis: `${value.getMonth() + 1}/${value.getDate()}`, full };
}

function currentKey(view: View): string {
  const today = new Date();
  const key = dateKey(today);
  return groupKey(key, view);
}

function formatNumber(value: number): string {
  return Number(value.toFixed(1)).toString();
}

function progressLabel(task: Task | undefined): string {
  if (task?.status === "completed" || (task?.progress_percent ?? 0) >= 100) return "✓ 완료";
  if ((task?.progress_percent ?? 0) > 0) return `○ 진행 중 ${Math.round(task?.progress_percent ?? 0)}%`;
  return "· 대기";
}

function roundedTopPath(x: number, y: number, width: number, height: number): string {
  const bottom = y + height;
  const radius = Math.min(2, width / 2, height);
  return `M ${x} ${bottom} V ${y + radius} Q ${x} ${y} ${x + radius} ${y} H ${x + width - radius} Q ${x + width} ${y} ${x + width} ${y + radius} V ${bottom} Z`;
}

export function ProgressChart({ snapshot, tasks }: { snapshot: ScheduleSnapshot | null; tasks: Task[] }) {
  const [view, setView] = useState<View>("daily");
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState({ left: 16, top: 16 });
  const taskMap = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);

  const nodes = useMemo<ChartNode[]>(() => {
    const groups = new Map<string, Placement[]>();
    for (const placement of snapshot?.placements ?? []) {
      const key = groupKey(placement.date, view);
      groups.set(key, [...(groups.get(key) ?? []), placement]);
    }
    const now = currentKey(view);
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([key, placements]) => {
      const byTask = new Map<number, number>();
      for (const placement of placements) byTask.set(placement.task_id, (byTask.get(placement.task_id) ?? 0) + placement.hours);
      const assigned = placements.reduce((sum, placement) => sum + placement.hours, 0);
      const completed = placements.reduce((sum, placement) => {
        const progress = Math.max(0, Math.min(100, taskMap.get(placement.task_id)?.progress_percent ?? 0));
        return sum + placement.hours * progress / 100;
      }, 0);
      const labels = periodLabels(key, view);
      return {
        key, axisLabel: labels.axis, periodLabel: labels.full, assigned, completed,
        percent: assigned ? completed / assigned * 100 : 0, current: key === now,
        tasks: [...byTask.entries()].map(([taskId, hours]) => ({ task: taskMap.get(taskId), hours })).sort((a, b) => b.hours - a.hours),
      };
    });
  }, [snapshot, taskMap, view]);

  if (!snapshot || snapshot.placements.length === 0) {
    return (
      <section className="panel mb-5 p-6 text-center sm:p-8" aria-label="진행 공수 그래프">
        <h2 className="font-black">진행 공수</h2>
        <p className="mt-2 text-sm text-[#71807b]">일정을 생성하면 그래프가 표시됩니다</p>
        <a href="#schedule-planner" className="btn btn-primary mt-4">일정 생성으로 이동</a>
      </section>
    );
  }

  const chartLeft = 48;
  const chartTop = 38;
  const chartHeight = 220;
  const chartBottom = chartTop + chartHeight;
  const columnWidth = nodes.length <= 8 ? 64 : nodes.length <= 20 ? 50 : 40;
  const width = Math.max(620, chartLeft + nodes.length * columnWidth + 24);
  const maxValue = Math.max(1, ...nodes.map((node) => node.assigned));
  const axisMax = Math.ceil(maxValue / 2) * 2;
  const active = nodes.find((node) => node.key === activeKey) ?? null;

  function showTooltip(key: string, target: SVGRectElement) {
    const rect = target.getBoundingClientRect();
    const tooltipWidth = Math.min(320, window.innerWidth - 32);
    const fitsRight = rect.right + 12 + tooltipWidth <= window.innerWidth - 16;
    const fitsLeft = rect.left - 12 - tooltipWidth >= 16;
    const left = fitsRight ? rect.right + 12 : fitsLeft ? rect.left - 12 - tooltipWidth : 16;
    const top = Math.max(12, Math.min(rect.top, window.innerHeight - 340));
    setTooltipPosition({ left, top });
    setActiveKey(key);
  }

  return (
    <section className="panel mb-5 overflow-hidden" aria-labelledby="progress-chart-title">
      <div className="flex flex-col gap-4 border-b border-[#e2e9e6] p-5 sm:flex-row sm:items-center sm:justify-between">
        <div><p className="eyebrow">Progress</p><h2 id="progress-chart-title" className="mt-1 text-xl font-black">배정 공수와 완료</h2><p className="mt-1 text-xs text-[#71807b]">막대의 전체 높이는 배정 공수, 초록 채움은 업무 진행률을 반영한 완료 공수입니다.</p></div>
        <div className="flex flex-col items-start gap-3 sm:items-end">
          <div className="flex gap-4 text-xs font-bold text-[#71807b]" aria-label="범례"><span className="flex items-center gap-1.5"><i className="size-3 bg-[#166a58]" aria-hidden />완료</span><span className="flex items-center gap-1.5"><i className="size-3 border border-[#d7e1dd] bg-[#e7edea]" aria-hidden />배정</span></div>
          <div className="flex gap-1 rounded-lg bg-[#edf2ef] p-1" role="tablist" aria-label="그래프 기간">{(["daily", "weekly", "monthly"] as View[]).map((item) => <button key={item} type="button" role="tab" aria-selected={view === item} onClick={() => { setView(item); setActiveKey(null); }} className={`rounded-md px-3 py-1.5 text-xs font-black ${view === item ? "bg-white text-[#166a58] shadow-sm" : "text-[#61716c]"}`}>{viewLabels[item]}</button>)}</div>
        </div>
      </div>
      <div className="overflow-x-auto p-3 sm:p-5">
        <div className="relative" style={{ width, minHeight: 315 }}>
          <svg viewBox={`0 0 ${width} 300`} width={width} height={300} role="img" aria-label={`${viewLabels[view]} 배정 공수와 완료 공수 막대 그래프`}>
            {[0, 1, 2, 3].map((step) => {
              const value = axisMax * (3 - step) / 3;
              const y = chartTop + chartHeight * step / 3;
              return <g key={step}><line x1={chartLeft} x2={width - 12} y1={y} y2={y} stroke="#eef2f0" strokeWidth="1" /><text x={chartLeft - 8} y={y + 4} textAnchor="end" fill="#71807b" fontSize="10">{formatNumber(value)}h</text></g>;
            })}
            <line x1={chartLeft} x2={width - 12} y1={chartBottom} y2={chartBottom} stroke="#dfe6e3" strokeWidth="1" />
            {nodes.map((node, index) => {
              const center = chartLeft + index * columnWidth + columnWidth / 2;
              const barWidth = Math.min(28, columnWidth - 4);
              const trackHeight = node.assigned / axisMax * chartHeight;
              const fillHeight = node.completed / axisMax * chartHeight;
              const trackY = chartBottom - trackHeight;
              const fillY = chartBottom - fillHeight;
              const labelY = Math.max(14, trackY - 7);
              const aria = `${node.periodLabel}, 배정 ${formatNumber(node.assigned)}시간, 완료 ${formatNumber(node.completed)}시간, 달성 ${Math.round(node.percent)}퍼센트`;
              return (
                <g key={node.key}>
                  <rect x={center - barWidth / 2} y={trackY} width={barWidth} height={trackHeight} rx="2" fill="#e7edea" stroke="#d7e1dd" strokeWidth="1" />
                  {fillHeight > 0 && <path d={roundedTopPath(center - barWidth / 2, fillY, barWidth, fillHeight)} fill="#166a58" />}
                  {nodes.length <= 14 && <text x={center} y={labelY} textAnchor="middle" fill="#71807b" fontSize="10" fontWeight="700">{Math.round(node.percent)}%</text>}
                  <text x={center} y={chartBottom + 22} textAnchor="middle" fill="#71807b" fontSize="10" fontWeight={node.current ? "800" : "500"}>{node.axisLabel}</text>
                  {node.current && <circle cx={center} cy={chartBottom + 34} r="2.5" fill="#166a58" />}
                  <rect x={center - columnWidth / 2} y={chartTop - 20} width={columnWidth} height={chartHeight + 64} fill="transparent" tabIndex={0} role="button" aria-label={aria} onMouseEnter={(event) => showTooltip(node.key, event.currentTarget)} onMouseLeave={() => setActiveKey((value) => value === node.key ? null : value)} onFocus={(event) => showTooltip(node.key, event.currentTarget)} onBlur={() => setActiveKey((value) => value === node.key ? null : value)} onClick={(event) => showTooltip(node.key, event.currentTarget)} onKeyDown={(event) => { if (event.key === "Escape") { setActiveKey(null); event.currentTarget.blur(); } }} />
                </g>
              );
            })}
          </svg>
          {active && (
            <div role="tooltip" className="pointer-events-none fixed z-50 w-[min(320px,calc(100vw-32px))] rounded-xl border border-[#dce5e1] bg-white p-4 text-[#26332f] shadow-xl" style={tooltipPosition}>
              <p className="font-black">{active.periodLabel}</p>
              <p className="mt-1 text-xs font-bold text-[#71807b]">배정 {formatNumber(active.assigned)}h · 완료 {formatNumber(active.completed)}h · 달성 {Math.round(active.percent)}%</p>
              <div className="mt-3 space-y-2 border-t border-[#e8eeeb] pt-3">{active.tasks.slice(0, 8).map(({ task, hours }, index) => <div key={task?.id ?? index} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 text-xs"><span className="whitespace-nowrap font-bold text-[#53635e]">{progressLabel(task)}</span><span className="truncate">{task?.title ?? "알 수 없는 업무"}</span><b>{formatNumber(hours)}h</b></div>)}{active.tasks.length > 8 && <p className="text-xs font-bold text-[#71807b]">외 {active.tasks.length - 8}개</p>}</div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
