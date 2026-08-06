"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { AnalysisReview, Dashboard, Project, ScheduleVersion, ScheduleVersionSummary, Task } from "@pacepm/shared-types";
import { ErrorState, LoadingState } from "@/components/feedback";
import { ProjectNav } from "@/components/project-nav";
import { ProgressChart } from "@/components/progress-chart";
import { SchedulePanel } from "@/components/schedule-panel";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatHours, isoToday, paceLabel, paceTone } from "@/lib/format";

export default function PlanPage() {
  const params = useParams<{ id: string }>(); const projectId = Number(params.id);
  const [project, setProject] = useState<Project | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [review, setReview] = useState<AnalysisReview>({ facts: [], tasks: [], source_blocks: [] });
  const [schedule, setSchedule] = useState<ScheduleVersion>({ version: null, schedule_snapshot: null });
  const [versions, setVersions] = useState<ScheduleVersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (initial = false) => {
    if (initial) setLoading(true); setError("");
    try {
      const [projectData, dashboardData, taskData, reviewData, scheduleData, versionData] = await Promise.all([
        api.projects.get(projectId), api.dashboard(projectId, isoToday()), api.tasks.list(projectId),
        api.analysis.review(projectId), api.schedule.get(projectId), api.schedule.versions(projectId),
      ]);
      setProject(projectData); setDashboard(dashboardData); setTasks(taskData);
      setReview(reviewData); setSchedule(scheduleData); setVersions(versionData);
    } catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(true), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  const weekStats = useMemo(() => {
    const now = new Date(); const start = new Date(now); start.setDate(now.getDate() - ((now.getDay() + 6) % 7)); start.setHours(0, 0, 0, 0);
    const end = new Date(start); end.setDate(start.getDate() + 6); end.setHours(23, 59, 59, 999);
    const due = tasks.filter((task) => { if (!["approved", "scheduled", "in_progress", "completed"].includes(task.status) || !task.planned_end_date) return false; const date = new Date(`${task.planned_end_date}T00:00:00`); return date >= start && date <= end; });
    return { count: due.length, completed: due.filter((task) => task.status === "completed").length };
  }, [tasks]);

  if (loading) return <main className="page-shell py-10"><LoadingState /></main>;
  if (!project || !dashboard) return <main className="page-shell py-10"><ErrorState message={error || "프로젝트를 불러올 수 없습니다."} onRetry={() => void load(true)} /></main>;
  const pace = dashboard.pace; const difference = pace.actual_progress_percent - pace.planned_progress_percent;
  const prediction = pace.forecast.estimated_completion_date ? formatDate(pace.forecast.estimated_completion_date) : "예측 데이터 부족 — 최소 3개 작업일 필요";

  return (
    <main className="page-shell py-8 sm:py-10">
      <ProjectNav projectId={projectId} projectName={project.name} />
      <div className="mb-7"><p className="eyebrow">Plan & control</p><h2 className="mt-2 text-3xl font-black tracking-[-.04em]">계획과 진행 현황</h2><p className="mt-2 text-sm text-[#687874]">실행 일정에서 업무를 직접 추가·체크하고, 진행 현황을 확인하세요.</p></div>
      {error && <div className="mb-4"><ErrorState message={error} onRetry={() => void load()} /></div>}

      <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-label="프로젝트 대시보드">
        <DashboardCard label="전체 진행률" value={`${Math.round(pace.actual_progress_percent)}%`} detail={`예정 ${Math.round(pace.planned_progress_percent)}%`} progress={pace.actual_progress_percent} />
        <DashboardCard label="계획 대비 차이" value={`${difference >= 0 ? "+" : ""}${difference.toFixed(1)}%p`} detail={difference >= 0 ? "계획보다 앞섬" : "계획보다 지연"} danger={difference < -5} />
        <DashboardCard label="예상 완료일" value={prediction} detail={pace.forecast.status === "insufficient_data" ? "완료 기록을 쌓으면 예측됩니다" : `목표 ${formatDate(project.target_date)}`} compact />
        <DashboardCard label="위험도" value={paceLabel[pace.status] ?? pace.status} detail={pace.delay_days ? `${pace.delay_days}일 지연 예상` : "현재 페이스 기준"} badge={paceTone(pace.status)} />
        <DashboardCard label="이번 주 완료율" value={`${weekStats.count ? Math.round(weekStats.completed / weekStats.count * 100) : 0}%`} detail={`${weekStats.completed}/${weekStats.count}개 완료`} />
        <DashboardCard label="남은 공수" value={formatHours(pace.forecast.remaining_hours)} detail={pace.forecast.required_daily_hours == null ? "작업 가능일 없음" : `목표까지 하루 ${formatHours(pace.forecast.required_daily_hours)} 필요`} />
      </section>

      <ProgressChart snapshot={schedule.schedule_snapshot} tasks={tasks} />

      {schedule.schedule_snapshot?.infeasible && <div className="warning-banner mb-5"><b>⚠ 목표일 준수 불가</b><span className="ml-2 text-sm">현재 가용시간으로 모든 업무를 배치할 수 없습니다. 아래 일정에서 재계획 조정안을 선택하세요.</span></div>}
      <div className="space-y-5">
        <SchedulePanel project={project} tasks={tasks} facts={review.facts} schedule={schedule} versions={versions} onChange={() => load()} />
      </div>
    </main>
  );
}

function DashboardCard({ label, value, detail, progress, danger, compact, badge }: { label: string; value: string; detail: string; progress?: number; danger?: boolean; compact?: boolean; badge?: string }) {
  return <article className="panel min-h-32 p-4"><p className="text-[11px] font-black uppercase tracking-[.06em] text-[#788681]">{label}</p><div className="mt-3">{badge ? <span className={`badge ${badge}`}>{value}</span> : <p className={`${compact ? "text-sm leading-5" : "text-xl"} font-black ${danger ? "text-[#ad3838]" : ""}`}>{value}</p>}</div>{progress !== undefined && <div className="progress-track mt-3"><div className="progress-fill" style={{ width: `${Math.min(100, progress)}%` }} /></div>}<p className="mt-2 text-[11px] leading-4 text-[#71807b]">{detail}</p></article>;
}
