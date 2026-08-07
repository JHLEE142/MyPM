"use client";

import { useMemo, useState } from "react";
import type { ScheduleSnapshot, Task } from "@mypm/shared-types";

type Point = { date: string; planned: number; done: number };

function dateKey(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function parseDate(value: string): Date {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

function shortLabel(value: string): string {
  const d = parseDate(value);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function buildSeries(snapshot: ScheduleSnapshot | null, tasks: Task[], startDate: string, targetDate: string): Point[] {
  const plannedByDate = new Map<string, number>();
  for (const placement of snapshot?.placements ?? []) {
    const key = placement.date.slice(0, 10);
    plannedByDate.set(key, (plannedByDate.get(key) ?? 0) + placement.hours);
  }
  const doneByDate = new Map<string, number>();
  for (const task of tasks) {
    if (task.status === "completed" && task.actual_end_date) {
      const key = task.actual_end_date.slice(0, 10);
      doneByDate.set(key, (doneByDate.get(key) ?? 0) + task.estimated_hours);
    }
  }
  const allDates = [...plannedByDate.keys(), ...doneByDate.keys(), startDate.slice(0, 10), targetDate.slice(0, 10)];
  const from = parseDate(allDates.reduce((min, d) => (d < min ? d : min)));
  const to = parseDate(allDates.reduce((max, d) => (d > max ? d : max)));
  const points: Point[] = [];
  let planned = 0;
  let done = 0;
  const cursor = new Date(from);
  // 하루 단위 누적 시리즈 (기간이 길어도 최대 ~120 포인트로 안전)
  let guard = 0;
  while (cursor <= to && guard < 366) {
    const key = dateKey(cursor);
    planned += plannedByDate.get(key) ?? 0;
    done += doneByDate.get(key) ?? 0;
    points.push({ date: key, planned, done });
    cursor.setDate(cursor.getDate() + 1);
    guard += 1;
  }
  return points;
}

export function ProjectAreaChart({ snapshot, tasks, startDate, targetDate }: {
  snapshot: ScheduleSnapshot | null; tasks: Task[]; startDate: string; targetDate: string;
}) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const points = useMemo(() => buildSeries(snapshot, tasks, startDate, targetDate), [snapshot, tasks, startDate, targetDate]);

  const width = 260;
  const height = 84;
  const padTop = 8;
  const padBottom = 16;
  const plotHeight = height - padTop - padBottom;

  if (points.length < 2) {
    return <div className="grid h-[84px] place-items-center rounded-lg bg-[#f4f7f5] text-[11px] font-bold text-[#87938f]">일정 데이터 없음</div>;
  }

  const maxValue = Math.max(1, ...points.map((p) => p.planned), ...points.map((p) => p.done));
  const x = (index: number) => (index / (points.length - 1)) * width;
  const y = (value: number) => padTop + plotHeight - (value / maxValue) * plotHeight;
  const line = (pick: (p: Point) => number) => points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(pick(p)).toFixed(1)}`).join(" ");
  const area = (pick: (p: Point) => number) => `${line(pick)} L ${width} ${padTop + plotHeight} L 0 ${padTop + plotHeight} Z`;

  const todayKey = dateKey(new Date());
  const todayIndex = points.findIndex((p) => p.date === todayKey);
  const hover = hoverIndex === null ? null : points[hoverIndex];
  const last = points[points.length - 1];
  const donePercent = last.planned > 0 ? Math.round((Math.min(last.done, last.planned) / last.planned) * 100) : 0;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={`계획 누적 ${Math.round(last.planned)}시간 대비 완료 누적 ${Math.round(last.done)}시간, 달성 ${donePercent}퍼센트`}
        onMouseLeave={() => setHoverIndex(null)}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = (event.clientX - rect.left) / rect.width;
          setHoverIndex(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
        }}
      >
        <path d={area((p) => p.planned)} fill="#e7edea" />
        <path d={line((p) => p.planned)} fill="none" stroke="#c6d3ce" strokeWidth="1.5" />
        <path d={area((p) => p.done)} fill="#166a58" fillOpacity="0.22" />
        <path d={line((p) => p.done)} fill="none" stroke="#166a58" strokeWidth="2" />
        {todayIndex >= 0 && (
          <line x1={x(todayIndex)} x2={x(todayIndex)} y1={padTop} y2={padTop + plotHeight} stroke="#9db3ab" strokeWidth="1" strokeDasharray="3 3" />
        )}
        {hoverIndex !== null && (
          <>
            <line x1={x(hoverIndex)} x2={x(hoverIndex)} y1={padTop} y2={padTop + plotHeight} stroke="#53635e" strokeWidth="1" />
            <circle cx={x(hoverIndex)} cy={y(points[hoverIndex].done)} r="3.5" fill="#166a58" stroke="#fff" strokeWidth="1.5" />
            <circle cx={x(hoverIndex)} cy={y(points[hoverIndex].planned)} r="3" fill="#aebfb9" stroke="#fff" strokeWidth="1.5" />
          </>
        )}
        <text x="0" y={height - 3} fill="#87938f" fontSize="9">{shortLabel(points[0].date)}</text>
        <text x={width} y={height - 3} textAnchor="end" fill="#87938f" fontSize="9">{shortLabel(last.date)}</text>
      </svg>
      {hover && (
        <div
          role="tooltip"
          className="pointer-events-none absolute -top-1 z-20 -translate-y-full rounded-lg border border-[#dce5e1] bg-white px-3 py-2 text-[11px] leading-5 text-[#26332f] shadow-lg"
          style={{ left: `min(max(0%, ${((hoverIndex ?? 0) / (points.length - 1)) * 100}% - 60px), calc(100% - 130px))` }}
        >
          <b>{shortLabel(hover.date)}</b>
          <span className="ml-2 text-[#71807b]">계획 {Math.round(hover.planned)}h · 완료 {Math.round(hover.done)}h</span>
        </div>
      )}
    </div>
  );
}
