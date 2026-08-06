"use client";

import { useMemo, useRef, useState } from "react";
import type { Project, ProjectFact, ReplanRequest, ScheduleComparison, ScheduleVersion, ScheduleVersionSummary, Task, TaskPriority } from "@pacepm/shared-types";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatFullDate, formatHours, isoToday, priorityLabel, taskStatusLabel, taskStatusTone } from "@/lib/format";
import { EmptyState, ErrorState } from "./feedback";

type View = "daily" | "weekly" | "monthly";
const strategies: Array<{ id: ReplanRequest["strategy"]; title: string; description: string }> = [
  { id: "redistribute", title: "남은 기간에 재배치", description: "완료·잠금·고정 업무를 보호하고 미완료 업무를 다시 배치합니다." },
  { id: "increase_capacity", title: "하루 가용시간 늘리기", description: "일일 가용시간을 늘린 조건으로 새 버전을 만듭니다." },
  { id: "defer_low_priority", title: "낮은 우선순위 이월", description: "낮은 우선순위 업무의 이월을 고려해 재배치합니다." },
  { id: "change_target", title: "목표일 변경", description: "새 목표일을 기준으로 남은 업무를 배치합니다." },
];
const planStatuses = new Set(["approved", "scheduled", "in_progress", "completed"]);
type DragKind = "monthly" | "weekly" | "leaf";

// 다른 주제로의 이동 허용 규칙: 리프(일간·기타) → 다른 일간 위/주간 그룹, 주간 → 다른 주간 위/월간 그룹
function canMoveAcross(dragKind: DragKind, targetKind: DragKind): boolean {
  if (dragKind === "leaf") return targetKind === "leaf" || targetKind === "weekly";
  if (dragKind === "weekly") return targetKind === "weekly" || targetKind === "monthly";
  return false;
}

function localDateKey(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function SchedulePanel({ project, tasks, facts, schedule, versions, onChange }: { project: Project; tasks: Task[]; facts: ProjectFact[]; schedule: ScheduleVersion; versions: ScheduleVersionSummary[]; onChange: () => Promise<void> | void }) {
  const [view, setView] = useState<View>("daily");
  const [replanOpen, setReplanOpen] = useState(false);
  const [strategy, setStrategy] = useState<ReplanRequest["strategy"]>("redistribute");
  const [capacity, setCapacity] = useState(project.daily_capacity_hours + 1);
  const [targetDate, setTargetDate] = useState(project.target_date);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [comparison, setComparison] = useState<ScheduleComparison | null>(null);
  const [fromVersion, setFromVersion] = useState<number>(versions.at(-2)?.version ?? versions[0]?.version ?? 1);
  const [toVersion, setToVersion] = useState<number>(versions.at(-1)?.version ?? 1);
  const [newTitle, setNewTitle] = useState("");
  const [newHours, setNewHours] = useState(1);
  const [newPriority, setNewPriority] = useState<TaskPriority>("medium");
  const [newDueDate, setNewDueDate] = useState("");
  const [taskBusyId, setTaskBusyId] = useState<number | "new" | "all" | null>(null);
  const [dragging, setDragging] = useState<{ group: string; id: number; kind: DragKind } | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null);
  // 핸들러는 렌더 클로저가 아닌 ref에서 최신 드래그 상태를 읽는다 (state는 스타일 표시 전용)
  const draggingRef = useRef<{ group: string; id: number; kind: DragKind } | null>(null);
  const snapshot = schedule.schedule_snapshot;
  const taskMap = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);
  const listTasks = useMemo(
    () => tasks.filter((task) => planStatuses.has(task.status) || ["on_hold", "blocked"].includes(task.status)),
    [tasks],
  );
  const childrenByParent = useMemo(() => {
    const result = new Map<number, Task[]>();
    for (const task of listTasks) {
      if (task.parent_task_id == null) continue;
      result.set(task.parent_task_id, [...(result.get(task.parent_task_id) ?? []), task]);
    }
    return result;
  }, [listTasks]);
  const taskStats = useMemo(() => {
    const result = new Map<number, { hours: number; completedHours: number; progress: number }>();
    const visit = (task: Task): { hours: number; completedHours: number; progress: number } => {
      const cached = result.get(task.id);
      if (cached) return cached;
      const children = childrenByParent.get(task.id) ?? [];
      const stats = children.length
        ? children.map(visit).reduce(
            (sum, item) => ({ hours: sum.hours + item.hours, completedHours: sum.completedHours + item.completedHours, progress: 0 }),
            { hours: 0, completedHours: 0, progress: 0 },
          )
        : { hours: task.estimated_hours, completedHours: task.estimated_hours * task.progress_percent / 100, progress: task.progress_percent };
      stats.progress = stats.hours > 0 ? stats.completedHours / stats.hours * 100 : 0;
      result.set(task.id, stats);
      return stats;
    };
    for (const task of listTasks) visit(task);
    return result;
  }, [childrenByParent, listTasks]);
  const leafListTasks = useMemo(() => listTasks.filter((task) => !childrenByParent.has(task.id)), [childrenByParent, listTasks]);
  const monthlyRoots = useMemo(
    () => listTasks.filter((task) => task.cadence === "monthly" && task.parent_task_id == null),
    [listTasks],
  );
  const flatTasks = useMemo(
    () => listTasks.filter((task) => task.cadence == null && task.parent_task_id == null),
    [listTasks],
  );

  const daily = useMemo(() => {
    const groups = new Map<string, typeof snapshot extends null ? never : NonNullable<typeof snapshot>["placements"]>();
    for (const item of snapshot?.placements ?? []) {
      const task = taskMap.get(item.task_id);
      if (task && planStatuses.has(task.status)) groups.set(item.date, [...(groups.get(item.date) ?? []), item]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [snapshot, taskMap]);
  const weekly = useMemo(() => {
    const groups = new Map<string, typeof daily>();
    for (const [date, items] of daily) {
      const day = new Date(`${date}T00:00:00`); const monday = new Date(day); monday.setDate(day.getDate() - ((day.getDay() + 6) % 7));
      const key = localDateKey(monday); groups.set(key, [...(groups.get(key) ?? []), [date, items]]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [daily]);
  const monthly = useMemo(() => {
    const dateTasks = leafListTasks.filter((task) => planStatuses.has(task.status) && (task.due_date || task.planned_end_date)).sort((a, b) => String(a.due_date || a.planned_end_date).localeCompare(String(b.due_date || b.planned_end_date)));
    const groups = new Map<string, Task[]>();
    for (const task of dateTasks) { const date = task.due_date || task.planned_end_date || ""; const key = date.slice(0, 7); groups.set(key, [...(groups.get(key) ?? []), task]); }
    return [...groups.entries()];
  }, [leafListTasks]);

  async function generate() {
    setBusy(true); setError("");
    try { await api.schedule.generate(project.id); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function replan() {
    setBusy(true); setError("");
    const payload: ReplanRequest = { strategy, reason: `사용자 선택: ${strategies.find((item) => item.id === strategy)?.title}` };
    if (strategy === "increase_capacity") payload.daily_capacity_hours = capacity;
    if (strategy === "change_target") payload.target_date = targetDate;
    try { await api.schedule.replan(project.id, payload); setReplanOpen(false); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function addTask(event: React.FormEvent) {
    event.preventDefault();
    if (!newTitle.trim()) return;
    setTaskBusyId("new"); setError("");
    try {
      await api.tasks.create(project.id, {
        title: newTitle.trim(),
        estimated_hours: newHours,
        priority: newPriority,
        due_date: newDueDate || null,
      });
      setNewTitle(""); setNewHours(1); setNewPriority("medium"); setNewDueDate("");
      await onChange();
    } catch (e) { setError(errorMessage(e)); }
    finally { setTaskBusyId(null); }
  }

  async function toggleComplete(task: Task, checked: boolean) {
    setTaskBusyId(task.id); setError("");
    try {
      if (checked) await api.tasks.complete(task.id, { completed_date: isoToday() });
      else await api.tasks.reopen(task.id);
      await onChange();
    } catch (e) { setError(errorMessage(e)); }
    finally { setTaskBusyId(null); }
  }

  async function removeTask(task: Task) {
    const includesChildren = childrenByParent.has(task.id);
    const message = includesChildren
      ? `“${task.title}” 업무와 모든 하위 업무를 삭제할까요? 되돌릴 수 없습니다.`
      : `“${task.title}” 업무를 삭제할까요?`;
    if (!window.confirm(message)) return;
    setTaskBusyId(task.id); setError("");
    try { await api.tasks.remove(task.id); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setTaskBusyId(null); }
  }

  async function reorderSiblings(siblings: Task[], draggedId: number, targetId: number) {
    if (draggedId === targetId) return;
    const ids = siblings.map((task) => task.id);
    const from = ids.indexOf(draggedId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ...ids.splice(from, 1));
    setError("");
    try { await api.tasks.reorder(project.id, ids); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
  }

  async function moveTask(taskId: number, newParentId: number | null, beforeTaskId?: number | null) {
    setError("");
    try { await api.tasks.move(taskId, newParentId, beforeTaskId); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
  }

  function dragProps(group: string, task: Task, siblings: Task[], kind: DragKind, parentId: number | null) {
    // 트리가 중첩 draggable이므로 stopPropagation으로 상위 행의 핸들러 실행(드래그 상태 덮어쓰기)을 막는다.
    return {
      draggable: true,
      onDragStart: (event: React.DragEvent) => {
        event.stopPropagation();
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(task.id));
        draggingRef.current = { group, id: task.id, kind };
        setDragging(draggingRef.current);
      },
      onDragEnd: (event: React.DragEvent) => {
        event.stopPropagation();
        draggingRef.current = null;
        setDragging(null);
        setDragOverId(null);
      },
      onDragOver: (event: React.DragEvent) => {
        const current = draggingRef.current;
        if (!current || current.id === task.id) return;
        if (current.group === group || canMoveAcross(current.kind, kind)) {
          event.preventDefault();
          event.stopPropagation();
          setDragOverId(task.id);
        }
      },
      onDragLeave: (event: React.DragEvent) => { event.stopPropagation(); setDragOverId((value) => (value === task.id ? null : value)); },
      onDrop: (event: React.DragEvent) => {
        event.preventDefault();
        event.stopPropagation();
        setDragOverId(null);
        const current = draggingRef.current;
        draggingRef.current = null;
        setDragging(null);
        if (!current || current.id === task.id) return;
        if (current.group === group) {
          void reorderSiblings(siblings, current.id, task.id);
          return;
        }
        if (!canMoveAcross(current.kind, kind)) return;
        // 다른 주제로 이동: 대상이 같은 계층 행이면 그 앞에, 컨테이너 행이면 그 안의 맨 뒤에
        if (current.kind === "leaf") {
          if (kind === "leaf") void moveTask(current.id, parentId, task.id);
          else void moveTask(current.id, task.id, null); // weekly 컨테이너로
        } else if (current.kind === "weekly") {
          if (kind === "weekly") void moveTask(current.id, parentId, task.id);
          else void moveTask(current.id, task.id, null); // monthly 컨테이너로
        }
      },
    };
  }

  function dragHighlight(task: Task): string {
    return dragOverId === task.id ? " outline outline-2 outline-[#166a58] outline-offset-1" : dragging?.id === task.id ? " opacity-50" : "";
  }

  async function removeAllTasks() {
    if (!window.confirm(`이 프로젝트의 업무 ${tasks.length}개를 전부 삭제할까요? 되돌릴 수 없습니다.`)) return;
    setTaskBusyId("all"); setError("");
    try { await api.tasks.removeAll(project.id); await onChange(); }
    catch (e) { setError(errorMessage(e)); }
    finally { setTaskBusyId(null); }
  }

  async function compare() {
    if (fromVersion === toVersion) { setError("서로 다른 버전을 선택해 주세요."); return; }
    setBusy(true); setError("");
    try { setComparison(await api.schedule.compare(project.id, fromVersion, toVersion)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  return (
    <section id="schedule-planner" className="panel scroll-mt-5 overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-[#e2e9e6] p-5 md:flex-row md:items-center md:justify-between">
        <div><p className="eyebrow">Schedule</p><h2 className="mt-1 text-xl font-black">실행 일정</h2><p className="mt-1 text-xs text-[#71807b]">목록과 기간별 그룹으로 확인합니다. 완료 업무와 고정 일정은 재계획에서 보호됩니다.</p></div>
        <div className="flex flex-wrap gap-2"><button type="button" className="btn btn-secondary" disabled={busy || leafListTasks.filter((task) => ["approved", "scheduled", "in_progress", "blocked"].includes(task.status)).length === 0} onClick={() => void generate()}>{schedule.version ? "일정 다시 생성" : "일정 생성"}</button><button type="button" className="btn btn-primary" disabled={busy || !schedule.version} onClick={() => setReplanOpen((value) => !value)}>재계획</button><button type="button" className="btn btn-ghost btn-sm self-center text-[#a33a36]" disabled={taskBusyId === "all" || tasks.length === 0} onClick={() => void removeAllTasks()}>{taskBusyId === "all" ? "삭제 중…" : "전체 삭제"}</button></div>
      </div>

      <form onSubmit={(event) => void addTask(event)} className="grid gap-2 border-b border-[#e2e9e6] bg-[#f7faf8] p-4 sm:grid-cols-[minmax(0,1fr)_86px_96px_150px_auto]">
        <input className="field" value={newTitle} maxLength={500} placeholder="새 업무 제목을 입력하고 Enter" aria-label="새 업무 제목" onChange={(event) => setNewTitle(event.target.value)} />
        <div className="relative"><input className="field pr-8" type="number" min="0" max="10000" step="0.5" value={newHours} aria-label="예상 공수" onChange={(event) => setNewHours(Number(event.target.value))} /><span className="absolute right-3 top-3 text-xs text-[#71807b]">h</span></div>
        <select className="field" value={newPriority} aria-label="우선순위" onChange={(event) => setNewPriority(event.target.value as TaskPriority)}>{(["critical", "high", "medium", "low"] as TaskPriority[]).map((value) => <option key={value} value={value}>{priorityLabel[value]}</option>)}</select>
        <input className="field" type="date" value={newDueDate} aria-label="기한 (선택)" onChange={(event) => setNewDueDate(event.target.value)} />
        <button className="btn btn-primary" disabled={taskBusyId === "new" || !newTitle.trim()}>{taskBusyId === "new" ? "추가 중…" : "＋ 추가"}</button>
      </form>

      {listTasks.length > 0 && (
        <div className="border-b border-[#e2e9e6]">
          <div className="flex items-center justify-between px-4 pt-4"><h3 className="text-sm font-black">업무 목록 <span className="font-bold text-[#71807b]">({leafListTasks.filter((task) => task.status === "completed").length}/{leafListTasks.length} 완료)</span></h3></div>
          <div className="space-y-3 p-4">
            {monthlyRoots.map((monthlyTask) => {
              const monthlyStats = taskStats.get(monthlyTask.id) ?? { hours: monthlyTask.estimated_hours, progress: 0, completedHours: 0 };
              const weeklyTasks = childrenByParent.get(monthlyTask.id) ?? [];
              return (
                <div key={monthlyTask.id} className={`rounded-xl border border-[#dfe7e4] bg-white p-3${dragHighlight(monthlyTask)}`} {...dragProps("monthly", monthlyTask, monthlyRoots, "monthly", null)}>
                  <div className="flex items-center gap-2.5">
                    <span className="cursor-grab select-none text-[#9db3ab]" aria-hidden title="드래그하여 순서 변경">⠿</span>
                    <span className="text-base text-[#166a58]" aria-hidden>●</span>
                    <div className="min-w-0 flex-1"><p className="text-sm font-black">{monthlyTask.title}</p><p className="text-[11px] text-[#71807b]">파생 진도 {Math.round(monthlyStats.progress)}% · 총 {formatHours(monthlyStats.hours)}</p></div>
                    <button type="button" className="shrink-0 px-1 text-xs font-bold text-[#a33a36] hover:underline" disabled={taskBusyId === monthlyTask.id} aria-label={`${monthlyTask.title} 하위 포함 삭제`} onClick={() => void removeTask(monthlyTask)}>하위 포함 삭제</button>
                  </div>
                  <div className="mt-2 space-y-2 pl-5">
                    {weeklyTasks.map((weeklyTask) => {
                      const weeklyStats = taskStats.get(weeklyTask.id) ?? { hours: weeklyTask.estimated_hours, progress: 0, completedHours: 0 };
                      return (
                        <div key={weeklyTask.id} className={`rounded-lg bg-[#f7faf8] p-2.5${dragHighlight(weeklyTask)}`} {...dragProps(`weekly-${monthlyTask.id}`, weeklyTask, weeklyTasks, "weekly", monthlyTask.id)}>
                          <div className="flex items-center gap-2.5">
                            <span className="cursor-grab select-none text-[#9db3ab]" aria-hidden title="드래그하여 순서 변경">⠿</span>
                            <span className="text-[#3b7c6c]" aria-hidden>◦</span>
                            <div className="min-w-0 flex-1"><p className="text-sm font-bold">{weeklyTask.title}</p><p className="text-[11px] text-[#71807b]">{Math.round(weeklyStats.progress)}% · {formatHours(weeklyStats.hours)}</p></div>
                            <button type="button" className="shrink-0 px-1 text-xs font-bold text-[#a33a36] hover:underline" disabled={taskBusyId === weeklyTask.id} aria-label={`${weeklyTask.title} 하위 포함 삭제`} onClick={() => void removeTask(weeklyTask)}>하위 포함 삭제</button>
                          </div>
                          <div className="mt-1.5 space-y-1 pl-5">
                            {(childrenByParent.get(weeklyTask.id) ?? []).map((dailyTask) => (
                              <div key={dailyTask.id} className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${dailyTask.status === "completed" ? "bg-[#eef6f2]" : "bg-white"}${dragHighlight(dailyTask)}`} {...dragProps(`daily-${weeklyTask.id}`, dailyTask, childrenByParent.get(weeklyTask.id) ?? [], "leaf", weeklyTask.id)}>
                                <span className="cursor-grab select-none text-[#9db3ab]" aria-hidden title="드래그하여 순서 변경">⠿</span>
                                <span className="text-[#71807b]" aria-hidden>·</span>
                                <input type="checkbox" className="size-4 shrink-0 accent-[#166a58]" checked={dailyTask.status === "completed"} disabled={taskBusyId === dailyTask.id} aria-label={`${dailyTask.title} 완료 체크`} onChange={(event) => void toggleComplete(dailyTask, event.target.checked)} />
                                <p className={`min-w-0 flex-1 truncate text-sm ${dailyTask.status === "completed" ? "text-[#6e837b] line-through" : "font-medium"}`}>{dailyTask.title}</p>
                                <span className="shrink-0 text-[11px] font-bold text-[#71807b]">{formatHours(dailyTask.estimated_hours)}</span>
                                <button type="button" className="shrink-0 px-1 text-xs font-bold text-[#a33a36] hover:underline" disabled={taskBusyId === dailyTask.id} aria-label={`${dailyTask.title} 삭제`} onClick={() => void removeTask(dailyTask)}>삭제</button>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {dragging?.kind === "leaf" && flatTasks.length === 0 && (
              <div
                className="rounded-xl border-2 border-dashed border-[#c9d8d2] bg-[#f8faf9] p-4 text-center text-xs font-bold text-[#5e706a]"
                onDragOver={(event) => { event.preventDefault(); }}
                onDrop={(event) => {
                  event.preventDefault();
                  const current = draggingRef.current;
                  draggingRef.current = null; setDragging(null); setDragOverId(null);
                  if (current?.kind === "leaf") void moveTask(current.id, null, null);
                }}
              >
                여기에 놓으면 기타 업무로 이동합니다
              </div>
            )}
            {flatTasks.length > 0 && (
              <div className="rounded-xl border border-[#dfe7e4] bg-[#f8faf9] p-3">
                <h4 className="mb-2 text-xs font-black text-[#5e706a]">기타 업무</h4>
                <div className="space-y-1">
                  {flatTasks.map((task) => (
                    <div key={task.id} className={`flex items-center gap-2 rounded-lg px-2.5 py-2 ${task.status === "completed" ? "bg-[#eef6f2]" : "bg-white"}${dragHighlight(task)}`} {...dragProps("flat", task, flatTasks, "leaf", null)}>
                      <span className="cursor-grab select-none text-[#9db3ab]" aria-hidden title="드래그하여 순서 변경">⠿</span>
                      <span className="text-[#71807b]" aria-hidden>·</span>
                      <input type="checkbox" className="size-4 shrink-0 accent-[#166a58]" checked={task.status === "completed"} disabled={taskBusyId === task.id} aria-label={`${task.title} 완료 체크`} onChange={(event) => void toggleComplete(task, event.target.checked)} />
                      <div className="min-w-0 flex-1"><p className={`truncate text-sm font-bold ${task.status === "completed" ? "text-[#6e837b] line-through" : ""}`}>{task.title}</p><p className="text-[11px] text-[#71807b]">{formatHours(task.estimated_hours)} · {Math.round(task.progress_percent)}%{task.due_date ? ` · 기한 ${formatDate(task.due_date)}` : ""}{task.status !== "completed" && !["approved", "scheduled"].includes(task.status) ? ` · ${taskStatusLabel[task.status] ?? task.status}` : ""}</p></div>
                      <button type="button" className="shrink-0 px-1 text-xs font-bold text-[#a33a36] hover:underline" disabled={taskBusyId === task.id} aria-label={`${task.title} 삭제`} onClick={() => void removeTask(task)}>삭제</button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      {error && <div className="p-4 pb-0"><ErrorState message={error} /></div>}
      {snapshot?.infeasible && <div className="m-4 mb-0 warning-banner"><b>⚠ 현재 조건으로 목표일 준수 불가</b><p className="mt-1 text-sm">배치하지 못한 업무 {snapshot.unscheduled.length}개, 남은 공수 {formatHours(snapshot.unscheduled.reduce((sum, item) => sum + item.remaining_hours, 0))}. 재계획에서 가용시간 또는 목표일 조정을 선택하세요.</p></div>}
      {!!snapshot?.deferred?.length && <div className="m-4 mb-0 rounded-xl border border-[#d9e3df] bg-[#f7faf8] p-4"><b>이월 업무</b><ul className="mt-2 space-y-1 text-sm">{snapshot.deferred.map((item) => <li key={item.task_id}>• {taskMap.get(item.task_id)?.title ?? `업무 #${item.task_id}`} — 낮은 우선순위·미시작 업무</li>)}</ul></div>}
      {replanOpen && <div className="m-4 rounded-2xl border border-[#cddcd6] bg-[#f6f9f7] p-5"><h3 className="font-black">조정안 선택</h3><p className="mt-1 text-xs text-[#71807b]">선택한 안으로 기존 일정을 덮어쓰지 않고 새 버전을 만듭니다.</p><div className="mt-4 grid gap-2 md:grid-cols-2">{strategies.map((item) => <label key={item.id} className={`cursor-pointer rounded-xl border p-3 ${strategy === item.id ? "border-[#166a58] bg-white" : "border-[#dbe4e0]"}`}><input className="mr-2 accent-[#166a58]" type="radio" name="strategy" checked={strategy === item.id} onChange={() => setStrategy(item.id)} /><b className="text-sm">{item.title}</b><p className="ml-6 mt-1 text-xs leading-5 text-[#6d7c77]">{item.description}</p></label>)}</div>{strategy === "increase_capacity" && <label className="mt-4 block max-w-xs"><span className="label">새 하루 가용시간</span><input className="field" type="number" min="0.5" step="0.5" value={capacity} onChange={(e) => setCapacity(Number(e.target.value))} /></label>}{strategy === "change_target" && <label className="mt-4 block max-w-xs"><span className="label">새 목표일</span><input className="field" type="date" min={project.start_date} value={targetDate} onChange={(e) => setTargetDate(e.target.value)} /></label>}<div className="mt-5 flex gap-2"><button type="button" className="btn btn-primary" disabled={busy} onClick={() => void replan()}>{busy ? "새 버전 생성 중…" : "선택한 조정안 적용"}</button><button type="button" className="btn btn-secondary" onClick={() => setReplanOpen(false)}>취소</button></div></div>}

      <div className="flex flex-col gap-3 border-b border-[#e2e9e6] p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex w-fit gap-1 rounded-lg bg-[#edf2ef] p-1">{(["daily", "weekly", "monthly"] as View[]).map((item) => <button key={item} type="button" onClick={() => setView(item)} className={`rounded-md px-3 py-1.5 text-xs font-black ${view === item ? "bg-white text-[#166a58] shadow-sm" : "text-[#61716c]"}`}>{item === "daily" ? "일간" : item === "weekly" ? "주간" : "월간"}</button>)}</div>
        {schedule.version && <span className="text-xs font-bold text-[#71807b]">현재 일정 버전 v{schedule.version} · 유효 가용시간 {formatHours(snapshot?.effective_daily_capacity_hours ?? 0)}</span>}
      </div>

      {!snapshot || snapshot.placements.length === 0 ? <EmptyState title="생성된 일정이 없습니다" description="승인된 업무를 추가한 뒤 일정 생성 버튼을 누르세요." /> : (
        <div className="p-4 sm:p-5">
          {view === "daily" && <div className="space-y-4">{daily.map(([date, placements]) => <div key={date} className="grid gap-3 border-b border-[#e7edea] pb-4 md:grid-cols-[150px_1fr]"><div><p className="font-black">{formatFullDate(date)}</p><p className="mt-1 text-xs text-[#71807b]">총 {formatHours(placements.reduce((sum, item) => sum + item.hours, 0))}</p></div><div className="space-y-2">{placements.map((item, index) => { const task = taskMap.get(item.task_id); return <div key={`${item.task_id}-${index}`} className="flex items-center justify-between gap-3 rounded-lg bg-[#f5f8f6] px-3 py-2"><div className="flex min-w-0 items-center gap-2.5">{task && <input type="checkbox" className="size-4 shrink-0 accent-[#166a58]" checked={task.status === "completed"} disabled={taskBusyId === task.id} aria-label={`${task.title} 완료 체크`} onChange={(event) => void toggleComplete(task, event.target.checked)} />}<div className="min-w-0"><p className={`truncate text-sm font-bold ${task?.status === "completed" ? "text-[#6e837b] line-through" : ""}`}>{task?.title ?? `업무 #${item.task_id}`}</p><span className={`badge mt-1 ${taskStatusTone(task?.status ?? "approved")}`}>{taskStatusLabel[task?.status ?? "approved"] ?? task?.status}</span></div></div><div className="flex shrink-0 items-center gap-2"><b className="text-xs">{formatHours(item.hours)}</b>{task && <button type="button" className="text-xs font-bold text-[#a33a36] hover:underline" disabled={taskBusyId === task.id} aria-label={`${task.title} 삭제`} onClick={() => void removeTask(task)}>삭제</button>}</div></div>; })}</div></div>)}</div>}
          {view === "weekly" && <div className="space-y-4">{weekly.map(([week, dates]) => { const ids = [...new Set(dates.flatMap(([, items]) => items.map((item) => item.task_id)))]; const completed = ids.filter((id) => taskMap.get(id)?.status === "completed").length; return <article key={week} className="card p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-black">{formatDate(week)} 시작 주</h3><p className="mt-1 text-xs text-[#71807b]">업무 {ids.length}개 · {dates.reduce((sum, [, items]) => sum + items.reduce((value, item) => value + item.hours, 0), 0).toFixed(1)}시간</p></div><span className="badge badge-success">완료율 {ids.length ? Math.round(completed / ids.length * 100) : 0}%</span></div><div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">{dates.map(([date, items]) => <div key={date} className="rounded-lg bg-[#f4f7f5] p-3"><p className="text-xs font-black">{formatDate(date)}</p><div className="mt-2 space-y-1">{items.map((item) => <p key={`${item.task_id}-${date}`} className="truncate text-[11px] text-[#5f706a]">• {taskMap.get(item.task_id)?.title ?? `업무 #${item.task_id}`}</p>)}</div></div>)}</div></article>; })}</div>}
          {view === "monthly" && <div className="space-y-5">{facts.filter((fact) => fact.fact_type === "deadline" && fact.review_status === "approved").length > 0 && <div className="warning-banner"><p className="text-xs font-black uppercase tracking-[.08em]">목표 · 마일스톤</p><ul className="mt-2 space-y-1 text-sm">{facts.filter((fact) => fact.fact_type === "deadline" && fact.review_status === "approved").map((fact) => <li key={fact.id}>• {fact.content}</li>)}</ul></div>}{monthly.length === 0 ? <EmptyState title="기한이 설정된 업무가 없습니다" description="업무 수정에서 기한을 설정하면 월간 목록에 표시됩니다." /> : monthly.map(([month, monthTasks]) => <article key={month}><h3 className="mb-2 text-lg font-black">{month.replace("-", ".")} 월간 일정</h3><div className="divide-y divide-[#e6ece9] rounded-xl border border-[#dfe7e4]">{monthTasks.map((task) => <div key={task.id} className="flex items-center justify-between gap-3 p-3"><div><p className="text-sm font-bold">{task.title}</p><p className="mt-1 text-xs text-[#71807b]">{task.milestone_id ? `마일스톤 #${task.milestone_id}` : "프로젝트 업무"}</p></div><div className="text-right"><b className="text-xs">{formatDate(task.due_date || task.planned_end_date)}</b><span className={`badge ml-2 ${taskStatusTone(task.status)}`}>{taskStatusLabel[task.status] ?? task.status}</span></div></div>)}</div></article>)}</div>}
        </div>
      )}

      <div className="border-t border-[#e2e9e6] bg-[#f8faf9] p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><h3 className="font-black">일정 버전</h3><p className="mt-1 text-xs text-[#71807b]">{versions.length ? versions.map((item) => `v${item.version} ${item.reason}`).join(" · ") : "아직 저장된 일정 버전이 없습니다."}</p></div>{versions.length >= 2 && <div className="flex flex-wrap items-end gap-2"><label><span className="label">이전</span><select className="field py-2" value={fromVersion} onChange={(e) => setFromVersion(Number(e.target.value))}>{versions.map((item) => <option key={item.version} value={item.version}>v{item.version}</option>)}</select></label><label><span className="label">이후</span><select className="field py-2" value={toVersion} onChange={(e) => setToVersion(Number(e.target.value))}>{versions.map((item) => <option key={item.version} value={item.version}>v{item.version}</option>)}</select></label><button type="button" className="btn btn-secondary" onClick={() => void compare()}>비교</button></div>}</div>
        {comparison && <div className="mt-4 rounded-xl border border-[#d9e3df] bg-white p-4"><p className="text-sm font-black">v{comparison.from_version} → v{comparison.to_version} 변경 {comparison.changes.length}건</p>{comparison.changes.length === 0 ? <p className="mt-2 text-xs text-[#71807b]">배치 변경이 없습니다.</p> : <div className="mt-3 space-y-2">{comparison.changes.map((change) => <div key={change.task_id} className="grid gap-1 text-xs sm:grid-cols-[1fr_1fr_1fr]"><b>{taskMap.get(change.task_id)?.title ?? `업무 #${change.task_id}`}</b><span className="text-[#7a8884]">이전 {change.before.map(([date, hours]) => `${formatDate(date)} ${hours}h`).join(", ") || "미배정"}</span><span className="text-[#166a58]">이후 {change.after.map(([date, hours]) => `${formatDate(date)} ${hours}h`).join(", ") || "미배정"}</span></div>)}</div>}</div>}
      </div>
    </section>
  );
}
