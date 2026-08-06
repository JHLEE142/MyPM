"use client";

import { useMemo, useState } from "react";
import type { AnalysisReview, ProjectFact, SourceDocument, Task, TaskPriority } from "@pacepm/shared-types";
import { api, errorMessage } from "@/lib/api";
import { priorityLabel, taskStatusLabel } from "@/lib/format";
import { EmptyState, ErrorState } from "./feedback";

type Selection = { kind: "task"; data: Task } | { kind: "fact"; data: ProjectFact };
const categories = [
  { id: "goal", label: "목표" }, { id: "deliverable", label: "산출물" }, { id: "deadline", label: "마일스톤" },
  { id: "task", label: "업무" }, { id: "risk", label: "위험" }, { id: "open_question", label: "미확정 질문" }, { id: "constraint", label: "충돌" },
] as const;

export function ReviewPanel({ projectId, review, sources, onChange }: { projectId: number; review: AnalysisReview; sources: SourceDocument[]; onChange: () => Promise<void> | void }) {
  const [category, setCategory] = useState("goal");
  const [selected, setSelected] = useState<Selection | null>(null);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const items = useMemo<Selection[]>(() => category === "task"
    ? review.tasks.map((data) => ({ kind: "task", data }))
    : review.facts.filter((fact) => fact.fact_type === category).map((data) => ({ kind: "fact", data })), [category, review]);
  const current = selected && items.some((item) => item.kind === selected.kind && item.data.id === selected.data.id)
    ? selected
    : items[0] ?? null;

  const sourceBlockId = current?.kind === "task" ? current.data.source_links[0]?.source_block_id : current?.data.source_block_id;
  const block = review.source_blocks.find((item) => item.id === sourceBlockId);
  const source = sources.find((item) => item.id === block?.source_document_id);

  function count(id: string) { return id === "task" ? review.tasks.length : review.facts.filter((fact) => fact.fact_type === id).length; }

  async function decide(action: "approve" | "modify" | "reject" | "hold", formData?: FormData) {
    if (!current) return;
    setBusy(true); setError("");
    try {
      const updates: Record<string, unknown> = {};
      if (formData && current.kind === "task") {
        updates.title = String(formData.get("title") ?? "").trim();
        updates.estimated_hours = Number(formData.get("estimated_hours"));
        updates.priority = String(formData.get("priority"));
      } else if (formData) updates.content = String(formData.get("content") ?? "").trim();
      await api.analysis.approve(projectId, current.kind === "task"
        ? { tasks: [{ id: current.data.id, action, updates }], facts: [] }
        : { tasks: [], facts: [{ id: current.data.id, action, updates }] });
      setEdit(false); await onChange();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  const confidence = current?.data.confidence ?? 0;
  return (
    <section className="panel overflow-hidden">
      <div className="border-b border-[#e2e9e6] p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="eyebrow">AI review</p><h2 className="mt-1 text-xl font-black">AI 분석 검토</h2></div><span className="text-xs font-bold text-[#6e7e79]">승인 전에는 일정에 반영되지 않습니다.</span></div></div>
      {error && <div className="p-4 pb-0"><ErrorState message={error} /></div>}
      <div className="grid min-h-[510px] lg:grid-cols-[180px_minmax(240px,1fr)_minmax(300px,1.25fr)]">
        <nav className="border-b border-[#e2e9e6] bg-[#f6f8f7] p-3 lg:border-b-0 lg:border-r" aria-label="분석 항목 분류">
          <div className="flex gap-2 overflow-x-auto lg:block lg:space-y-1">
            {categories.map((item) => <button type="button" key={item.id} onClick={() => { setCategory(item.id); setSelected(null); setEdit(false); }} className={`flex min-w-max items-center justify-between gap-4 rounded-lg px-3 py-2.5 text-left text-xs font-black lg:w-full ${category === item.id ? "bg-white text-[#166a58] shadow-sm" : "text-[#63736e] hover:bg-white/70"}`}><span>{item.id === "constraint" ? "⚠ " : ""}{item.label}</span><span className="rounded-full bg-[#e7edea] px-2 py-0.5 text-[10px] text-[#65756f]">{count(item.id)}</span></button>)}
          </div>
        </nav>
        <div className="border-b border-[#e2e9e6] lg:border-b-0 lg:border-r">
          <div className="border-b border-[#edf1ef] px-4 py-3 text-[11px] font-black uppercase tracking-[.1em] text-[#81908b]">추출 항목</div>
          {items.length === 0 ? <EmptyState title="항목이 없습니다" description="이 분류에서 추출된 내용이 없습니다." /> : <div className="max-h-[470px] divide-y divide-[#edf1ef] overflow-y-auto">{items.map((item) => {
            const active = current?.kind === item.kind && current.data.id === item.data.id;
            const title = item.kind === "task" ? item.data.title : item.data.content;
            const status = item.kind === "task" ? item.data.status : item.data.review_status;
            return <button type="button" key={`${item.kind}-${item.data.id}`} onClick={() => { setSelected(item); setEdit(false); }} className={`w-full p-4 text-left ${active ? "bg-[#edf6f2]" : "hover:bg-[#fafcfb]"}`}><div className="flex gap-2"><span className="mt-1 text-[#166a58]">{category === "constraint" ? "⚠" : "•"}</span><div className="min-w-0"><p className="line-clamp-2 text-sm font-bold leading-5">{title}</p><p className="mt-2 text-[11px] text-[#7a8884]">{taskStatusLabel[status] ?? (status === "approved" ? "승인됨" : status === "rejected" ? "거절됨" : "검토 대기")} · 신뢰도 {Math.round((item.data.confidence ?? 0) * 100)}%</p></div></div></button>;
          })}</div>}
        </div>
        <div className="p-5">
          {!current ? <EmptyState title="검토할 항목을 선택하세요" description="왼쪽 분류와 중앙 목록에서 항목을 선택하면 근거가 표시됩니다." /> : (
            <div className="flex h-full flex-col">
              <div className="flex-1">
                <div className="flex items-center justify-between gap-3"><span className="badge badge-neutral">{current.kind === "task" ? "업무 후보" : categories.find((item) => item.id === category)?.label}</span><span className="text-xs font-black text-[#48665d]">신뢰도 {Math.round(confidence * 100)}%</span></div>
                <div className="progress-track mt-2"><div className="progress-fill" style={{ width: `${confidence * 100}%` }} /></div>
                {edit ? (
                  <form className="mt-5 space-y-4" action={(formData) => void decide("modify", formData)}>
                    {current.kind === "task" ? <><label><span className="label">제목</span><input className="field" name="title" defaultValue={current.data.title} required /></label><div className="grid grid-cols-2 gap-3"><label><span className="label">예상 공수</span><input className="field" name="estimated_hours" type="number" min="0" step="0.5" defaultValue={current.data.estimated_hours} /></label><label><span className="label">우선순위</span><select className="field" name="priority" defaultValue={current.data.priority}>{(["critical", "high", "medium", "low"] as TaskPriority[]).map((priority) => <option key={priority} value={priority}>{priorityLabel[priority]}</option>)}</select></label></div></> : <label><span className="label">내용</span><textarea className="field min-h-28" name="content" defaultValue={current.data.content} required /></label>}
                    <div className="flex gap-2"><button className="btn btn-primary" disabled={busy}>수정 후 승인</button><button type="button" className="btn btn-secondary" onClick={() => setEdit(false)}>취소</button></div>
                  </form>
                ) : <div className="mt-5"><h3 className="text-lg font-black leading-7">{current.kind === "task" ? current.data.title : current.data.content}</h3>{current.kind === "task" && <p className="mt-2 text-sm leading-6 text-[#667670]">{current.data.description || "설명이 없습니다."}</p>}</div>}

                <div className="mt-6 rounded-xl border border-[#dfe7e4] bg-[#f7f9f8] p-4"><p className="text-[11px] font-black uppercase tracking-[.1em] text-[#71817c]">원문 근거</p>{block ? <><blockquote className="mt-3 border-l-2 border-[#57907f] pl-3 text-sm leading-6 text-[#42534e]">{block.content}</blockquote><div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[#788681]"><span>문서: <b>{source?.file_name ?? `문서 #${block.source_document_id}`}</b></span><span>위치: <b>{block.page_number ? `${block.page_number}쪽` : block.sheet_name ? `${block.sheet_name} 시트` : block.section_title || `${block.block_order + 1}번째 블록`}</b></span></div></> : <p className="mt-3 text-sm text-[#788681]">연결된 원문 블록이 없습니다.</p>}</div>
                {category === "constraint" && <div className="warning-banner mt-4 text-sm"><b>충돌 항목입니다.</b> 이 내용을 기준으로 채택할지, 프로젝트에서 제외할지 선택하세요.</div>}
              </div>
              {!edit && <div className="mt-5 flex flex-wrap gap-2 border-t border-[#e5ebe8] pt-4"><button type="button" className="btn btn-primary" disabled={busy} onClick={() => void decide("approve")}>{category === "constraint" ? "이 내용 채택" : "승인"}</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setEdit(true)}>수정</button><button type="button" className="btn btn-danger" disabled={busy} onClick={() => void decide("reject")}>{category === "constraint" ? "이 내용 제외" : "거절"}</button></div>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
