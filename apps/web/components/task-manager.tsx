"use client";

import { useState } from "react";
import type { Task, TaskCreate, TaskPriority, TaskStatus } from "@mypm/shared-types";
import { api, errorMessage } from "@/lib/api";
import { formatDate, priorityLabel, taskStatusLabel, taskStatusTone } from "@/lib/format";
import { EmptyState, ErrorState } from "./feedback";

const statusOptions: TaskStatus[] = ["approved", "scheduled", "in_progress", "on_hold", "blocked", "completed"];
const priorityOptions: TaskPriority[] = ["critical", "high", "medium", "low"];

export function TaskManager({ projectId, tasks, onChange }: { projectId: number; tasks: Task[]; onChange: () => Promise<void> | void }) {
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [error, setError] = useState("");

  async function create(formData: FormData) {
    setBusyId("new"); setError("");
    const payload: TaskCreate = {
      title: String(formData.get("title") ?? "").trim(),
      description: String(formData.get("description") ?? "").trim() || null,
      estimated_hours: Number(formData.get("estimated_hours")),
      priority: String(formData.get("priority")) as TaskPriority,
      due_date: String(formData.get("due_date") || "") || null,
      locked: formData.get("locked") === "on",
    };
    try { await api.tasks.create(projectId, payload); setShowForm(false); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusyId(null); }
  }

  async function update(taskId: number, formData: FormData) {
    setBusyId(taskId); setError("");
    try {
      await api.tasks.update(taskId, {
        title: String(formData.get("title") ?? "").trim(),
        description: String(formData.get("description") ?? "").trim() || null,
        estimated_hours: Number(formData.get("estimated_hours")),
        priority: String(formData.get("priority")) as TaskPriority,
        status: String(formData.get("status")) as TaskStatus,
        progress_percent: Number(formData.get("progress_percent")),
        due_date: String(formData.get("due_date") || "") || null,
        locked: formData.get("locked") === "on",
      });
      setEditing(null); await onChange();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusyId(null); }
  }

  async function changeStatus(task: Task, status: TaskStatus) {
    setBusyId(task.id); setError("");
    try {
      if (status === "completed") await api.tasks.complete(task.id);
      else if (status === "blocked") await api.tasks.block(task.id, "사용자가 업무 관리에서 차단 처리");
      else await api.tasks.update(task.id, { status });
      await onChange();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusyId(null); }
  }

  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e2e9e6] p-5">
        <div><h2 className="font-black">수동 업무 관리</h2><p className="mt-1 text-xs text-[#6d7d78]">AI 없이도 업무를 추가하고 상태를 관리할 수 있습니다.</p></div>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => setShowForm((value) => !value)}>＋ 업무 추가</button>
      </div>
      {error && <div className="p-4 pb-0"><ErrorState message={error} /></div>}
      {showForm && (
        <form action={create} className="grid gap-3 border-b border-[#e2e9e6] bg-[#f7faf8] p-5 md:grid-cols-6">
          <label className="md:col-span-2"><span className="label">업무명</span><input className="field" name="title" required placeholder="예: 요구사항 검토" /></label>
          <label><span className="label">예상 공수</span><input className="field" name="estimated_hours" type="number" min="0" step="0.5" defaultValue="1" required /></label>
          <label><span className="label">우선순위</span><select className="field" name="priority" defaultValue="medium">{priorityOptions.map((p) => <option key={p} value={p}>{priorityLabel[p]}</option>)}</select></label>
          <label><span className="label">기한</span><input className="field" name="due_date" type="date" /></label>
          <label className="flex items-end gap-2 pb-3 text-sm font-bold"><input name="locked" type="checkbox" className="size-4 accent-[#166a58]" /> 일정 잠금</label>
          <div className="flex items-end"><button disabled={busyId === "new"} className="btn btn-primary w-full">{busyId === "new" ? "추가 중…" : "추가"}</button></div>
          <label className="md:col-span-6"><span className="label">설명</span><textarea className="field min-h-20" name="description" placeholder="완료 기준이나 참고 사항" /></label>
        </form>
      )}
      {tasks.filter((task) => !["pending_review", "rejected", "extracted"].includes(task.status)).length === 0 ? (
        <EmptyState title="등록된 업무가 없습니다" description="첫 업무를 직접 추가하거나 자료를 분석해 업무 후보를 만드세요." />
      ) : (
        <div className="divide-y divide-[#e8eeeb]">
          {tasks.filter((task) => !["pending_review", "rejected", "extracted"].includes(task.status)).map((task) => editing?.id === task.id ? (
            <form key={task.id} action={(data) => update(task.id, data)} className="grid gap-3 bg-[#f7faf8] p-5 md:grid-cols-6">
              <label className="md:col-span-2"><span className="label">업무명</span><input className="field" name="title" required defaultValue={task.title} /></label>
              <label><span className="label">예상 공수</span><input className="field" name="estimated_hours" type="number" min="0" step="0.5" defaultValue={task.estimated_hours} /></label>
              <label><span className="label">우선순위</span><select className="field" name="priority" defaultValue={task.priority}>{priorityOptions.map((p) => <option key={p} value={p}>{priorityLabel[p]}</option>)}</select></label>
              <label><span className="label">상태</span><select className="field" name="status" defaultValue={task.status}>{statusOptions.map((s) => <option key={s} value={s}>{taskStatusLabel[s] ?? s}</option>)}</select></label>
              <label><span className="label">기한</span><input className="field" name="due_date" type="date" defaultValue={task.due_date ?? ""} /></label>
              <label className="flex items-end gap-2 pb-3 text-sm font-bold"><input name="locked" type="checkbox" defaultChecked={task.locked} className="size-4 accent-[#166a58]" /> 일정 잠금</label>
              <label className="md:col-span-4"><span className="label">설명</span><textarea className="field" name="description" defaultValue={task.description ?? ""} /></label>
              <label><span className="label">진행률 (%)</span><input className="field" name="progress_percent" type="number" min="0" max="100" defaultValue={task.progress_percent} /></label>
              <div className="flex items-end gap-2"><button disabled={busyId === task.id} className="btn btn-primary flex-1">저장</button><button type="button" className="btn btn-secondary" onClick={() => setEditing(null)}>취소</button></div>
            </form>
          ) : (
            <div key={task.id} className="flex flex-col gap-3 p-5 md:flex-row md:items-center">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2"><p className="font-bold">{task.title}</p><span className={`badge ${taskStatusTone(task.status)}`}>{taskStatusLabel[task.status] ?? task.status}</span>{task.ai_generated && <span className="badge badge-info">AI</span>}</div>
                <p className="mt-1 text-xs text-[#71807c]">{task.estimated_hours}시간 · {priorityLabel[task.priority]} · 기한 {formatDate(task.due_date)}</p>
                <div className="mt-3 flex items-center gap-3"><div className="progress-track max-w-64 flex-1"><div className="progress-fill" style={{ width: `${task.progress_percent}%` }} /></div><span className="text-xs font-bold">{Math.round(task.progress_percent)}%</span></div>
              </div>
              <div className="flex flex-wrap gap-2">
                <select aria-label={`${task.title} 상태 변경`} className="field w-auto py-2 text-xs" value={task.status} disabled={busyId === task.id} onChange={(event) => void changeStatus(task, event.target.value)}>
                  {statusOptions.map((status) => <option key={status} value={status}>{taskStatusLabel[status] ?? status}</option>)}
                </select>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setEditing(task)}>수정</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
