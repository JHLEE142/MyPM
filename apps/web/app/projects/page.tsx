"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dashboard, Project, ScheduleSnapshot, Task } from "@mypm/shared-types";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";
import { Pagination, usePagination } from "@/components/pagination";
import { ProjectAreaChart } from "@/components/project-area-chart";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatHours, isoToday, paceLabel, paceTone } from "@/lib/format";
import { useRefreshListener } from "@/lib/refresh";

interface ProjectRow { project: Project; dashboard: Dashboard | null; tasks: Task[]; snapshot: ScheduleSnapshot | null; }

export default function ProjectsPage() {
  const [rows, setRows] = useState<ProjectRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const projects = await api.projects.list();
      const detail = await Promise.all(projects.map(async (project) => {
        const [dashboard, tasks, schedule] = await Promise.all([
          api.dashboard(project.id, isoToday()).catch(() => null),
          api.tasks.list(project.id).catch(() => []),
          api.schedule.get(project.id).catch(() => null),
        ]);
        return { project, dashboard, tasks, snapshot: schedule?.schedule_snapshot ?? null };
      }));
      setRows(detail);
    } catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }, []);

  useRefreshListener(() => load(), { onWindowFocus: true });
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const chartPages = usePagination(rows);
  const listPages = usePagination(rows);

  const summary = useMemo(() => ({
    active: rows.filter(({ project }) => project.status === "active").length,
    risky: rows.filter(({ dashboard }) => dashboard && ["risk", "critical"].includes(dashboard.pace.status)).length,
    totalRemaining: rows.reduce((sum, { dashboard }) => sum + (dashboard?.pace.forecast.remaining_hours ?? 0), 0),
  }), [rows]);

  async function remove(row: ProjectRow) {
    if (!window.confirm(`“${row.project.name}” 프로젝트와 모든 자료·업무를 삭제할까요? 이 작업은 되돌릴 수 없습니다.`)) return;
    setDeleting(row.project.id); setError("");
    try { await api.projects.remove(row.project.id); setRows((current) => current.filter(({ project }) => project.id !== row.project.id)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setDeleting(null); }
  }

  return (
    <main className="page-shell py-10 sm:py-14">
      <div className="mb-8 flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div><p className="eyebrow">Portfolio overview</p><h1 className="mt-2 text-3xl font-black tracking-[-.045em] sm:text-4xl">프로젝트의 속도를 한눈에</h1><p className="mt-2 text-sm text-[#687874]">계획과 실제의 차이를 확인하고 다음 작업을 결정하세요.</p></div>
        <Link className="btn btn-primary" href="/projects/new">＋ 새 프로젝트 만들기</Link>
      </div>

      {!loading && rows.length > 0 && (
        <section className="mb-6" aria-label="프로젝트별 진행 현황">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-black text-[#40514c]">프로젝트별 진행 현황</h2>
            <div className="flex gap-4 text-[11px] font-bold text-[#71807b]" aria-label="범례">
              <span className="flex items-center gap-1.5"><i className="size-3 rounded-sm bg-[#166a58]/25 ring-1 ring-inset ring-[#166a58]" aria-hidden />완료 누적</span>
              <span className="flex items-center gap-1.5"><i className="size-3 rounded-sm bg-[#e7edea] ring-1 ring-inset ring-[#c6d3ce]" aria-hidden />계획 누적</span>
              <span className="flex items-center gap-1.5"><i className="h-3 border-l border-dashed border-[#9db3ab]" aria-hidden />오늘</span>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {chartPages.pageItems.map(({ project, dashboard, tasks, snapshot }) => {
              const status = dashboard?.pace.status ?? "warning";
              return (
                <Link key={project.id} href={`/projects/${project.id}/plan`} className="card block p-4 transition-shadow hover:shadow-md">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-black">{project.name}</span>
                    <span className={`badge shrink-0 ${paceTone(status)}`}>{paceLabel[status] ?? "주의"}</span>
                  </div>
                  <ProjectAreaChart snapshot={snapshot} tasks={tasks} startDate={project.start_date} targetDate={project.target_date} />
                  <p className="mt-2 text-[11px] font-bold text-[#71807b]">
                    진행 {Math.round(dashboard?.pace.actual_progress_percent ?? 0)}%
                    {project.owner ? ` · 담당 ${project.owner}` : ""}
                  </p>
                </Link>
              );
            })}
          </div>
          <Pagination {...chartPages} onChange={chartPages.setPage} label="진행 현황 카드" />
        </section>
      )}

      {!loading && rows.length > 0 && (
        <section className="mb-6 grid gap-3 sm:grid-cols-3" aria-label="프로젝트 요약">
          <div className="card p-4"><p className="text-xs font-bold text-[#73817d]">진행 중</p><p className="mt-2 text-2xl font-black">{summary.active}<span className="ml-1 text-sm font-medium text-[#73817d]">개</span></p></div>
          <div className="card p-4"><p className="text-xs font-bold text-[#73817d]">위험 프로젝트</p><p className="mt-2 text-2xl font-black text-[#a33838]">{summary.risky}<span className="ml-1 text-sm font-medium text-[#73817d]">개</span></p></div>
          <div className="card p-4"><p className="text-xs font-bold text-[#73817d]">총 남은 공수</p><p className="mt-2 text-2xl font-black">{formatHours(summary.totalRemaining)}</p></div>
        </section>
      )}

      {error && <div className="mb-5"><ErrorState message={error} onRetry={() => void load()} /></div>}
      {loading ? <LoadingState /> : rows.length === 0 ? (
        <section className="panel"><EmptyState title="아직 프로젝트가 없습니다" description="프로젝트의 목표와 가용시간을 입력하면 오늘 실행할 수 있는 계획으로 정리해 드립니다." action={<Link href="/projects/new" className="btn btn-primary">첫 프로젝트 만들기</Link>} /></section>
      ) : (
        <section className="panel overflow-hidden">
          <div className="hidden grid-cols-[minmax(220px,1.4fr)_1fr_1fr_1fr_48px] gap-5 border-b border-[#e1e8e5] bg-[#f4f7f5] px-5 py-3 text-[11px] font-black uppercase tracking-[.08em] text-[#71807b] lg:grid">
            <span>프로젝트</span><span>진행 상태</span><span>일정</span><span>다음 마일스톤</span><span />
          </div>
          <div className="divide-y divide-[#e5ebe8]">
            {listPages.pageItems.map((row) => <ProjectListRow key={row.project.id} row={row} deleting={deleting === row.project.id} onDelete={() => void remove(row)} />)}
          </div>
          <Pagination {...listPages} onChange={listPages.setPage} label="프로젝트 목록" />
        </section>
      )}
    </main>
  );
}

function ProjectListRow({ row, deleting, onDelete }: { row: ProjectRow; deleting: boolean; onDelete: () => void }) {
  const { project, dashboard, tasks } = row;
  const pace = dashboard?.pace;
  const nextTask = tasks
    .filter((task) => ["approved", "scheduled", "in_progress"].includes(task.status) && (task.due_date || task.planned_end_date))
    .sort((a, b) => String(a.due_date || a.planned_end_date).localeCompare(String(b.due_date || b.planned_end_date)))[0];
  const actual = pace?.actual_progress_percent ?? 0;
  const planned = pace?.planned_progress_percent ?? 0;
  const status = pace?.status ?? "warning";
  const difference = actual - planned;
  return (
    <article className="grid gap-5 p-5 transition-colors hover:bg-[#fbfdfc] lg:grid-cols-[minmax(220px,1.4fr)_1fr_1fr_1fr_48px] lg:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2"><Link href={`/projects/${project.id}/today`} className="truncate text-[15px] font-black hover:text-[#166a58]">{project.name}</Link><span className={`badge ${paceTone(status)}`}>{paceLabel[status] ?? "주의"}</span></div>
        {project.owner && <p className="mt-1 text-xs font-bold text-[#536b63]">담당 {project.owner}</p>}
        <p className="mt-1 line-clamp-1 text-xs text-[#71807b]">{project.description || "설명이 없습니다."}</p>
      </div>
      <div>
        <div className="mb-2 flex justify-between text-xs"><span className="font-bold">전체 {Math.round(actual)}%</span><span className="text-[#7a8884]">예정 {Math.round(planned)}%</span></div>
        <div className="progress-track"><div className="progress-fill" style={{ width: `${Math.min(100, actual)}%` }} /></div>
        <p className={`mt-1.5 text-[11px] font-bold ${difference < -5 ? "text-[#a33a36]" : "text-[#4e6a61]"}`}>{difference >= 0 ? "+" : ""}{difference.toFixed(1)}%p {difference >= 0 ? "계획보다 앞섬" : "계획보다 지연"}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs lg:block lg:space-y-1.5">
        <p><span className="text-[#7a8884]">목표일</span><br className="lg:hidden" /> <b className="lg:float-right">{formatDate(project.target_date)}</b></p>
        <p><span className="text-[#7a8884]">예상 완료</span><br className="lg:hidden" /> <b className="lg:float-right">{pace?.forecast.estimated_completion_date ? formatDate(pace.forecast.estimated_completion_date) : "데이터 부족"}</b></p>
      </div>
      <div className="text-xs">
        {nextTask ? <><p className="truncate font-bold">{nextTask.title}</p><p className="mt-1 text-[#71807b]">{formatDate(nextTask.due_date || nextTask.planned_end_date)} · {nextTask.estimated_hours}시간</p></> : <p className="text-[#7a8884]">예정된 마일스톤 없음</p>}
      </div>
      <button type="button" onClick={onDelete} disabled={deleting} aria-label={`${project.name} 삭제`} className="btn btn-ghost btn-sm justify-self-start text-[#a33a36] lg:justify-self-center">{deleting ? "…" : "삭제"}</button>
    </article>
  );
}
