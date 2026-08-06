"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Draft, DraftFields, DraftTaskCandidate, TaskPriority } from "@pacepm/shared-types";
import { ErrorState } from "@/components/feedback";
import { api, errorMessage } from "@/lib/api";

const weekdays = ["월", "화", "수", "목", "금", "토", "일"];

export function GuidedProjectSetup() {
  const router = useRouter();
  const creationStarted = useRef(false);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState("");
  const [nextQuestion, setNextQuestion] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (creationStarted.current) return;
    creationStarted.current = true;
    void api.drafts.create().then(setDraft).catch((caught) => setError(errorMessage(caught)));
  }, []);

  useEffect(() => { messagesEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [draft?.conversation.length, sending]);

  function localField<Key extends keyof DraftFields>(key: Key, value: DraftFields[Key]) {
    setDraft((current) => current ? { ...current, fields: { ...current.fields, [key]: value } } : current);
  }

  async function saveFields(fields: DraftFields, label: string) {
    if (!draft) return;
    const hasBlankValue = Object.values(fields).some((value) => typeof value === "string" && !value.trim());
    const hasBlankTaskTitle = fields.task_candidates?.some((task) => !task.title.trim()) ?? false;
    if (hasBlankValue || hasBlankTaskTitle) return;
    setSaving(label); setError("");
    try { setDraft(await api.drafts.update(draft.id, fields)); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setSaving(null); }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const content = message.trim();
    if (!draft || !content || sending) return;
    setMessage(""); setSending(true); setError("");
    setDraft((current) => current ? { ...current, conversation: [...current.conversation, { role: "user", content }] } : current);
    try {
      const result = await api.drafts.chat(draft.id, content);
      setNextQuestion(result.next_question);
      setDraft((current) => current ? {
        ...current,
        fields: result.fields,
        completeness_percent: result.completeness_percent,
        conversation: [...current.conversation, { role: "assistant", content: result.reply }],
      } : current);
    } catch (caught) {
      setMessage(content);
      setDraft((current) => {
        if (!current) return current;
        const conversation = [...current.conversation];
        const last = conversation.at(-1);
        if (last?.role === "user" && last.content === content) conversation.pop();
        return { ...current, conversation };
      });
      setError(errorMessage(caught));
    }
    finally { setSending(false); }
  }

  async function confirm() {
    if (!draft) return;
    setConfirming(true); setError("");
    try {
      const project = await api.drafts.confirm(draft.id);
      router.push(`/projects/${project.id}/today`);
    } catch (caught) { setError(errorMessage(caught)); setConfirming(false); }
  }

  if (!draft) {
    return <section className="panel grid min-h-[500px] place-items-center p-8">{error ? <ErrorState message={error} /> : <div className="text-center"><div className="mx-auto mb-3 size-8 animate-spin rounded-full border-4 border-[#d5e5df] border-t-[#166a58]" /><p className="text-sm font-bold text-[#687874]">등록 대화를 준비하고 있어요…</p></div>}</section>;
  }

  const fields = draft.fields;
  const requiredReady = Boolean(fields.name?.trim() && fields.start_date && fields.target_date && fields.daily_capacity_hours);
  const tasks = fields.task_candidates ?? [];

  function updateTask(index: number, values: Partial<DraftTaskCandidate>) {
    const next = tasks.map((task, taskIndex) => taskIndex === index ? { ...task, ...values } : task);
    localField("task_candidates", next);
  }

  function saveTasks(next = tasks) { void saveFields({ task_candidates: next }, "업무 후보"); }

  return (
    <div className="space-y-5">
      <section className="panel overflow-hidden px-5 py-4 sm:px-6">
        <div className="mb-2 flex items-center justify-between gap-4"><span className="text-sm font-black">등록 준비 {Math.round(draft.completeness_percent)}%</span><span className="text-xs font-bold text-[#71807b]">필수 정보 4개 + 선택 정보</span></div>
        <div className="progress-track h-2.5" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={draft.completeness_percent}><div className="progress-fill transition-[width] duration-300" style={{ width: `${draft.completeness_percent}%` }} /></div>
      </section>

      {error && <ErrorState message={error} />}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.12fr)_minmax(360px,.88fr)]">
        <section className="panel flex min-h-[620px] flex-col overflow-hidden">
          <div className="border-b border-[#e2e9e6] px-5 py-4"><p className="eyebrow">AI-guided setup</p><h2 className="mt-1 text-lg font-black">대화로 프로젝트 정보 채우기</h2></div>
          <div className="flex-1 space-y-4 overflow-y-auto bg-[#f7faf8] p-5" aria-live="polite">
            {draft.conversation.map((item, index) => (
              <div key={`${index}-${item.role}`} className={`flex ${item.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[86%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm ${item.role === "user" ? "rounded-br-md bg-[#166a58] text-white" : "rounded-bl-md border border-[#dbe6e1] bg-white text-[#33443f]"}`}>{item.content}</div>
              </div>
            ))}
            {sending && <div className="flex justify-start"><div className="rounded-2xl rounded-bl-md border border-[#dbe6e1] bg-white px-4 py-3 text-sm font-bold text-[#71807b]"><span className="mr-2 inline-flex gap-1" aria-hidden><i className="size-1.5 animate-pulse rounded-full bg-[#6b9588]" /><i className="size-1.5 animate-pulse rounded-full bg-[#6b9588] [animation-delay:150ms]" /><i className="size-1.5 animate-pulse rounded-full bg-[#6b9588] [animation-delay:300ms]" /></span>답변을 정리하고 있어요</div></div>}
            <div ref={messagesEnd} />
          </div>
          <form onSubmit={(event) => void send(event)} className="border-t border-[#e2e9e6] bg-white p-4">
            <div className="flex gap-2"><textarea className="field min-h-[48px] resize-none" rows={1} maxLength={4000} value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={nextQuestion ?? "예: 9월 1일부터 평일에 하루 4시간씩 진행할게요"} aria-label="프로젝트 정보 입력" /><button type="submit" className="btn btn-primary self-stretch px-5" disabled={!message.trim() || sending}>전송</button></div>
            <p className="mt-2 text-right text-[11px] font-bold text-[#87938f]">{message.length}/4000 · Shift+Enter 줄바꿈</p>
          </form>
        </section>

        <aside className="panel p-5 sm:p-6">
          <div className="mb-5 flex items-start justify-between"><div><p className="eyebrow">Live preview</p><h2 className="mt-1 text-lg font-black">프로젝트 미리보기</h2></div>{saving && <span className="badge badge-info">{saving} 저장 중</span>}</div>
          <div className="space-y-5">
            <PreviewText label="프로젝트명 *" value={fields.name ?? ""} placeholder="대화에서 확인 중" onChange={(value) => localField("name", value)} onSave={(value) => void saveFields({ name: value }, "프로젝트명")} />
            <PreviewText label="목표" value={fields.goal ?? ""} placeholder="대화에서 확인 중" multiline onChange={(value) => localField("goal", value)} onSave={(value) => void saveFields({ goal: value }, "목표")} />
            <PreviewText label="설명" value={fields.description ?? ""} placeholder="대화에서 확인 중" multiline onChange={(value) => localField("description", value)} onSave={(value) => void saveFields({ description: value }, "설명")} />
            <div className="grid grid-cols-2 gap-3"><PreviewDate label="시작일 *" value={fields.start_date ?? ""} onChange={(value) => localField("start_date", value)} onSave={(value) => void saveFields({ start_date: value }, "시작일")} /><PreviewDate label="목표일 *" value={fields.target_date ?? ""} onChange={(value) => localField("target_date", value)} onSave={(value) => void saveFields({ target_date: value }, "목표일")} /></div>
            <fieldset><legend className="label">작업 요일</legend><div className="flex flex-wrap gap-1.5">{weekdays.map((label, day) => <button key={label} type="button" aria-pressed={fields.work_days?.includes(day) ?? false} onClick={() => { const current = fields.work_days ?? []; const next = current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort(); if (next.length) { localField("work_days", next); void saveFields({ work_days: next }, "작업 요일"); } }} className={`grid size-9 place-items-center rounded-lg border text-xs font-black ${(fields.work_days ?? []).includes(day) ? "border-[#166a58] bg-[#e7f3ee] text-[#115545]" : "border-[#d7e1dd] text-[#87938f]"}`}>{label}</button>)}</div>{!fields.work_days && <p className="mt-1.5 text-xs text-[#87938f]">대화에서 확인 중</p>}</fieldset>
            <div className="grid grid-cols-2 gap-3"><label><span className="label">하루 가용시간 *</span><div className="relative"><input className="field pr-10" type="number" min="0.1" max="24" step="0.5" value={fields.daily_capacity_hours ?? ""} placeholder="확인 중" onChange={(event) => localField("daily_capacity_hours", Number(event.target.value))} onBlur={(event) => { if (!event.target.value) return; const value = Number(event.target.value); if (value > 0 && value <= 24) void saveFields({ daily_capacity_hours: value }, "가용시간"); }} /><span className="absolute right-3 top-3 text-xs text-[#71807b]">h</span></div></label><label><span className="label">버퍼</span><div className="relative"><input className="field pr-9" type="number" min="0" max="90" value={fields.buffer_ratio == null ? "" : Math.round(fields.buffer_ratio * 100)} placeholder="확인 중" onChange={(event) => localField("buffer_ratio", Number(event.target.value) / 100)} onBlur={(event) => { if (!event.target.value) return; const value = Number(event.target.value); if (value >= 0 && value <= 90) void saveFields({ buffer_ratio: value / 100 }, "버퍼"); }} /><span className="absolute right-3 top-3 text-xs text-[#71807b]">%</span></div></label></div>
            <div><span className="label">제외일</span><div className="flex flex-wrap gap-1.5">{(fields.excluded_dates ?? []).map((value) => <button key={value} type="button" className="badge badge-neutral" onClick={() => { const next = (fields.excluded_dates ?? []).filter((item) => item !== value); localField("excluded_dates", next); void saveFields({ excluded_dates: next }, "제외일"); }}>{value} ×</button>)}<input className="field mt-1" type="date" aria-label="제외일 추가" onChange={(event) => { if (!event.target.value) return; const next = [...new Set([...(fields.excluded_dates ?? []), event.target.value])].sort(); localField("excluded_dates", next); void saveFields({ excluded_dates: next }, "제외일"); event.target.value = ""; }} /></div>{!fields.excluded_dates && <p className="mt-1 text-xs text-[#87938f]">대화에서 확인 중</p>}</div>
            <div><div className="mb-2 flex items-center justify-between"><span className="label mb-0">업무 후보</span><button type="button" className="text-xs font-black text-[#166a58]" onClick={() => { const next = [...tasks, { title: "새 업무", estimated_hours: 1, priority: "medium" as TaskPriority }]; localField("task_candidates", next); saveTasks(next); }}>＋ 추가</button></div>{tasks.length ? <div className="space-y-2">{tasks.map((task, index) => <div key={index} className="grid grid-cols-[1fr_64px_84px_auto] gap-1.5 rounded-xl border border-[#e0e8e5] p-2"><input className="min-w-0 rounded-lg border border-[#d7e1dd] px-2 text-xs" value={task.title} aria-label={`업무 ${index + 1} 제목`} onChange={(event) => updateTask(index, { title: event.target.value })} onBlur={() => saveTasks()} /><input className="min-w-0 rounded-lg border border-[#d7e1dd] px-2 text-xs" type="number" min="0" max="10000" value={task.estimated_hours} aria-label={`업무 ${index + 1} 시간`} onChange={(event) => updateTask(index, { estimated_hours: Number(event.target.value) })} onBlur={(event) => { if (event.target.value) saveTasks(); }} /><select className="min-w-0 rounded-lg border border-[#d7e1dd] px-1 text-[11px]" value={task.priority} aria-label={`업무 ${index + 1} 우선순위`} onChange={(event) => { updateTask(index, { priority: event.target.value as TaskPriority }); }} onBlur={() => saveTasks()}><option value="critical">긴급</option><option value="high">높음</option><option value="medium">보통</option><option value="low">낮음</option></select><button type="button" className="px-1 text-[#a84b4b]" aria-label={`업무 ${index + 1} 삭제`} onClick={() => { const next = tasks.filter((_, taskIndex) => taskIndex !== index); localField("task_candidates", next); saveTasks(next); }}>×</button></div>)}</div> : <p className="rounded-xl bg-[#f4f7f5] px-3 py-3 text-xs text-[#87938f]">대화에서 확인 중</p>}</div>
          </div>

          <div className="mt-6 border-t border-[#e2e9e6] pt-5"><button type="button" className="btn btn-primary w-full" disabled={!requiredReady || confirming || sending} onClick={() => void confirm()}>{confirming ? "프로젝트 생성 중…" : "프로젝트 생성"}</button>{!requiredReady && <p className="mt-2 text-center text-xs font-bold text-[#87938f]">이름·시작일·목표일·가용시간이 필요합니다.</p>}</div>
        </aside>
      </div>
    </div>
  );
}

function PreviewText({ label, value, placeholder, multiline = false, onChange, onSave }: { label: string; value: string; placeholder: string; multiline?: boolean; onChange: (value: string) => void; onSave: (value: string) => void }) {
  const saveNonBlank = (next: string) => { if (next.trim()) onSave(next); };
  return <label><span className="label">{label}</span>{multiline ? <textarea className="field min-h-20 resize-y" value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} onBlur={(event) => saveNonBlank(event.target.value)} /> : <input className="field" value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} onBlur={(event) => saveNonBlank(event.target.value)} />}</label>;
}

function PreviewDate({ label, value, onChange, onSave }: { label: string; value: string; onChange: (value: string) => void; onSave: (value: string) => void }) {
  return <label><span className="label">{label}</span><input className="field" type="date" value={value} onChange={(event) => onChange(event.target.value)} onBlur={(event) => { if (event.target.value) onSave(event.target.value); }} /></label>;
}
