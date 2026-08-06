"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dashboard, Project, ScheduleVersionSummary, Task } from "@pacepm/shared-types";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";
import { ProjectNav } from "@/components/project-nav";
import { api, errorMessage } from "@/lib/api";
import { formatFullDate, formatHours, isoToday, taskStatusLabel } from "@/lib/format";

export default function TodayPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const [project, setProject] = useState<Project | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [versions, setVersions] = useState<ScheduleVersionSummary[]>([]);
  const [progress, setProgress] = useState<Record<number, number>>({});
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [projectData, dashboardData, taskData, versionData] = await Promise.all([
        api.projects.get(projectId), api.dashboard(projectId, isoToday()), api.tasks.list(projectId), api.schedule.versions(projectId),
      ]);
      setProject(projectData); setDashboard(dashboardData); setTasks(taskData); setVersions(versionData);
      setProgress(Object.fromEntries(taskData.map((task) => [task.id, task.progress_percent])));
    } catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  const taskMap = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
  const todayTasks = dashboard?.today.items.map((item) => ({ item, task: taskMap.get(item.task_id) })).filter((entry) => entry.task) ?? [];
  const completed = todayTasks.filter(({ task }) => task?.status === "completed").length;

  async function saveProgress(task: Task) {
    setBusyId(task.id); setError(""); setSaved("");
    try {
      await api.tasks.update(task.id, { progress_percent: progress[task.id], status: progress[task.id] > 0 ? "in_progress" : task.status });
      setSaved(`${task.title} 진행률을 저장했습니다.`); await load();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusyId(null); }
  }

  async function toggleComplete(task: Task, checked: boolean) {
    setBusyId(task.id); setError(""); setSaved("");
    try {
      if (checked) await api.tasks.complete(task.id, { note: notes[task.id]?.trim() || undefined, completed_date: isoToday() });
      else await api.tasks.reopen(task.id);
      await load();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusyId(null); }
  }

  if (loading) return <main className="page-shell py-10"><LoadingState /></main>;
  if (!project || !dashboard) return <main className="page-shell py-10"><ErrorState message={error || "프로젝트를 찾을 수 없습니다."} onRetry={() => void load()} /></main>;

  const excess = dashboard.today.excess_hours;
  const latestVersion = versions.at(-1);
  const scheduleStale = Boolean(latestVersion && Date.parse(project.updated_at) > Date.parse(latestVersion.created_at));
  return (
    <main className="page-shell py-8 sm:py-10">
      <ProjectNav projectId={projectId} projectName={project.name} />
      <div className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><p className="eyebrow">Today · {formatFullDate(dashboard.as_of)}</p><h2 className="mt-2 text-3xl font-black tracking-[-.04em]">오늘의 실행 계획</h2><p className="mt-2 text-sm text-[#687874]">작은 완료를 쌓아 프로젝트의 속도를 유지하세요.</p></div>
        <div className="card min-w-52 p-4"><div className="flex items-end justify-between"><span className="text-xs font-bold text-[#71807b]">오늘 완료</span><strong className="text-xl">{completed}/{todayTasks.length}</strong></div><div className="progress-track mt-3"><div className="progress-fill" style={{ width: `${todayTasks.length ? completed / todayTasks.length * 100 : 0}%` }} /></div></div>
      </div>

      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {scheduleStale && <div className="warning-banner mb-5"><b>프로젝트 설정이 변경되었습니다 — 일정을 다시 생성해야 반영됩니다</b><Link href={`/projects/${projectId}/plan#schedule-planner`} className="ml-3 text-sm font-black underline">일정 생성 버튼으로 이동</Link></div>}
      {saved && <div className="success-box mb-4">{saved}</div>}
      <section className="mb-5 grid gap-3 sm:grid-cols-2">
        <div className="panel p-5"><p className="text-xs font-bold text-[#71807b]">오늘 유효 가용시간</p><p className="mt-2 text-2xl font-black">{formatHours(dashboard.today.effective_daily_capacity_hours)}</p><p className="mt-1 text-xs text-[#71807b]">원 가용시간 {formatHours(dashboard.today.raw_daily_capacity_hours)} · 버퍼 {Math.round(project.buffer_ratio * 100)}%</p></div>
        <div className={`panel p-5 ${dashboard.today.over_capacity ? "border-[#e9bd73] bg-[#fffaf0]" : ""}`}><p className="text-xs font-bold text-[#71807b]">배정 합계</p><p className="mt-2 text-2xl font-black">{formatHours(dashboard.today.assigned_hours)}</p>{dashboard.today.over_capacity ? <p className="mt-1 font-black text-[#9a5809]">⚠ {formatHours(excess)} 초과 배정</p> : <p className="mt-1 text-xs font-bold text-[#247052]">가용시간 안에 배정되었습니다.</p>}</div>
      </section>

      <section className="panel overflow-hidden">
        <div className="border-b border-[#e2e9e6] p-5"><h2 className="font-black">오늘 예정 업무</h2><p className="mt-1 text-xs text-[#71807b]">체크하면 즉시 완료 처리되며, 메모도 완료 이력에 함께 기록됩니다.</p></div>
        {todayTasks.length === 0 ? <EmptyState title="오늘 배정된 업무가 없습니다" description="계획 화면에서 승인된 업무로 일정을 생성하거나 수동 업무를 추가하세요." action={<Link href={`/projects/${projectId}/plan`} className="btn btn-primary">계획으로 이동</Link>} /> : (
          <div className="divide-y divide-[#e5ebe8]">
            {todayTasks.map(({ item, task }) => task && (
              <article key={task.id} className={`p-5 ${task.status === "completed" ? "bg-[#f7faf8]" : ""}`}>
                <div className="flex items-start gap-4">
                  <input aria-label={`${task.title} 완료`} type="checkbox" className="mt-1 size-5 accent-[#166a58]" checked={task.status === "completed"} disabled={busyId === task.id} onChange={(e) => void toggleComplete(task, e.target.checked)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className={`font-black ${task.status === "completed" ? "text-[#7b8985] line-through" : ""}`}>{task.title}</h3><span className="badge badge-neutral">{formatHours(item.hours)}</span></div>
                    {task.description && <p className="mt-1 text-sm leading-6 text-[#687874]">{task.description}</p>}
                    <div className="mt-4 grid gap-3 md:grid-cols-[1fr_180px_auto] md:items-end">
                      <label><span className="label">진행률 {Math.round(progress[task.id] ?? task.progress_percent)}%</span><input aria-label={`${task.title} 진행률`} className="w-full accent-[#166a58]" type="range" min="0" max="100" step="5" disabled={task.status === "completed"} value={progress[task.id] ?? task.progress_percent} onChange={(e) => setProgress((current) => ({ ...current, [task.id]: Number(e.target.value) }))} /></label>
                      <label><span className="label">완료 메모</span><input className="field py-2" placeholder="완료 시 함께 기록" value={notes[task.id] ?? ""} onChange={(e) => setNotes((current) => ({ ...current, [task.id]: e.target.value }))} disabled={task.status === "completed"} /></label>
                      <button type="button" className="btn btn-secondary btn-sm" disabled={busyId === task.id || task.status === "completed"} onClick={() => void saveProgress(task)}>{busyId === task.id ? "저장 중…" : "진행률 저장"}</button>
                    </div>
                    <p className="mt-3 text-[11px] text-[#84908d]">현재 상태: {taskStatusLabel[task.status] ?? task.status}</p>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
