"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Project } from "@pacepm/shared-types";
import { ErrorState, LoadingState } from "@/components/feedback";
import { ProjectNav } from "@/components/project-nav";
import { api, errorMessage } from "@/lib/api";

const weekdays = ["월", "화", "수", "목", "금", "토", "일"];

export default function ProjectSettingsPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const [project, setProject] = useState<Project | null>(null);
  const [form, setForm] = useState<Project | null>(null);
  const [excludedInput, setExcludedInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scheduleNotice, setScheduleNotice] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const value = await api.projects.get(projectId);
      setProject(value);
      setForm(value);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setLoading(false); }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const scheduleChanged = useMemo(() => Boolean(project && form && (
    project.start_date !== form.start_date || project.target_date !== form.target_date ||
    project.daily_capacity_hours !== form.daily_capacity_hours || project.buffer_ratio !== form.buffer_ratio ||
    JSON.stringify(project.work_days) !== JSON.stringify(form.work_days) ||
    JSON.stringify(project.excluded_dates) !== JSON.stringify(form.excluded_dates)
  )), [project, form]);

  function update<K extends keyof Project>(key: K, value: Project[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current);
    setNotice("");
  }

  function toggleDay(day: number) {
    if (!form) return;
    update("work_days", form.work_days.includes(day) ? form.work_days.filter((value) => value !== day) : [...form.work_days, day].sort());
  }

  function addExcluded() {
    if (!form || !excludedInput || form.excluded_dates.includes(excludedInput)) return;
    update("excluded_dates", [...form.excluded_dates, excludedInput].sort());
    setExcludedInput("");
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!form || !form.name.trim()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = await api.projects.update(projectId, {
        name: form.name.trim(), owner: form.owner?.trim() || null, description: form.description?.trim() || null,
        start_date: form.start_date, target_date: form.target_date, work_days: form.work_days,
        daily_capacity_hours: Number(form.daily_capacity_hours), buffer_ratio: Number(form.buffer_ratio),
        excluded_dates: form.excluded_dates,
      });
      setScheduleNotice(scheduleChanged);
      setProject(saved); setForm(saved); setNotice("프로젝트 설정을 저장했습니다.");
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  if (loading) return <main className="page-shell py-10"><LoadingState /></main>;
  if (!project || !form) return <main className="page-shell py-10"><ErrorState message={error || "프로젝트를 찾을 수 없습니다."} onRetry={() => void load()} /></main>;

  return (
    <main className="page-shell py-8 sm:py-10">
      <ProjectNav projectId={projectId} projectName={project.name} />
      <div className="mb-7"><p className="eyebrow">Project settings</p><h2 className="mt-2 text-3xl font-black tracking-[-.04em]">프로젝트 설정</h2><p className="mt-2 text-sm text-[#687874]">프로젝트 기준은 언제든 변경할 수 있습니다.</p></div>
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {notice && <div className="success-box mb-4" role="status">{notice}</div>}
      {(scheduleChanged || scheduleNotice) && <div className="warning-banner mb-4" role="status">기간·작업 요일·가용시간·버퍼·제외 날짜 변경은 <b>일정을 다시 생성해야 반영됩니다.</b></div>}
      <form onSubmit={save} className="panel p-5 sm:p-7">
        <div className="grid gap-5 sm:grid-cols-2">
          <label><span className="label">프로젝트명</span><input className="field" value={form.name} maxLength={255} onChange={(event) => update("name", event.target.value)} /></label>
          <label><span className="label">담당자</span><input className="field" value={form.owner ?? ""} maxLength={100} onChange={(event) => update("owner", event.target.value)} /></label>
          <label className="sm:col-span-2"><span className="label">설명</span><textarea className="field min-h-28" value={form.description ?? ""} onChange={(event) => update("description", event.target.value)} /></label>
          <label><span className="label">시작일</span><input className="field" type="date" value={form.start_date} onChange={(event) => update("start_date", event.target.value)} /></label>
          <label><span className="label">목표일</span><input className="field" type="date" value={form.target_date} onChange={(event) => update("target_date", event.target.value)} /></label>
          <fieldset className="sm:col-span-2"><legend className="label">작업 요일</legend><div className="flex flex-wrap gap-2">{weekdays.map((label, day) => <button key={label} type="button" aria-pressed={form.work_days.includes(day)} onClick={() => toggleDay(day)} className={`grid size-11 place-items-center rounded-xl border text-sm font-black ${form.work_days.includes(day) ? "border-[#166a58] bg-[#166a58] text-white" : "border-[#d3dfda] bg-white text-[#71807b]"}`}>{label}</button>)}</div></fieldset>
          <label><span className="label">하루 가용시간</span><div className="relative"><input className="field pr-14" type="number" min="0.5" max="24" step="0.5" value={form.daily_capacity_hours} onChange={(event) => update("daily_capacity_hours", Number(event.target.value))} /><span className="absolute right-4 top-3 text-sm text-[#71807b]">시간</span></div></label>
          <label><span className="label">버퍼</span><div className="relative"><input className="field pr-12" type="number" min="0" max="99" value={Math.round(form.buffer_ratio * 100)} onChange={(event) => update("buffer_ratio", Number(event.target.value) / 100)} /><span className="absolute right-4 top-3 text-sm text-[#71807b]">%</span></div></label>
          <div className="sm:col-span-2"><span className="label">제외 날짜</span><div className="flex flex-wrap gap-2"><input className="field max-w-xs" type="date" value={excludedInput} onChange={(event) => setExcludedInput(event.target.value)} /><button type="button" className="btn btn-secondary" onClick={addExcluded}>추가</button></div>{form.excluded_dates.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{form.excluded_dates.map((value) => <button type="button" key={value} className="badge badge-neutral" onClick={() => update("excluded_dates", form.excluded_dates.filter((item) => item !== value))}>{value} ×</button>)}</div>}</div>
        </div>
        <div className="mt-7 border-t border-[#e2e9e6] pt-5"><button className="btn btn-primary" disabled={busy || !form.name.trim() || form.work_days.length === 0 || form.target_date < form.start_date}>{busy ? "저장 중…" : "설정 저장"}</button></div>
      </form>
    </main>
  );
}
