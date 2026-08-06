"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorState } from "@/components/feedback";
import { api, errorMessage } from "@/lib/api";
import { isoToday } from "@/lib/format";

const weekdays = ["월", "화", "수", "목", "금", "토", "일"];

export default function NewProjectPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [startDate, setStartDate] = useState(isoToday());
  const [targetDate, setTargetDate] = useState("");
  const [workDays, setWorkDays] = useState([0, 1, 2, 3, 4]);
  const [capacity, setCapacity] = useState(4);
  const [buffer, setBuffer] = useState(20);
  const [excludedDates, setExcludedDates] = useState<string[]>([]);
  const [excludedInput, setExcludedInput] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [sourceName, setSourceName] = useState("프로젝트 메모.md");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const stepValid = useMemo(() => {
    if (step === 1) return Boolean(name.trim() && startDate && targetDate && targetDate >= startDate);
    if (step === 2) return workDays.length > 0 && capacity > 0 && buffer >= 0 && buffer < 100;
    return true;
  }, [step, name, startDate, targetDate, workDays, capacity, buffer]);

  function toggleDay(day: number) {
    setWorkDays((current) => current.includes(day) ? current.filter((value) => value !== day) : [...current, day].sort());
  }

  function addExcluded() {
    if (excludedInput && !excludedDates.includes(excludedInput)) setExcludedDates((current) => [...current, excludedInput].sort());
    setExcludedInput("");
  }

  function next() {
    setError("");
    if (!stepValid) { setError(step === 1 ? "프로젝트명과 올바른 기간을 입력해 주세요." : "작업 요일과 가용시간을 확인해 주세요."); return; }
    setStep((current) => Math.min(3, current + 1));
  }

  async function finish() {
    setBusy(true); setError("");
    try {
      const project = await api.projects.create({
        name: name.trim(), description: description.trim() || null, start_date: startDate, target_date: targetDate,
        work_days: workDays, daily_capacity_hours: capacity, buffer_ratio: buffer / 100, excluded_dates: excludedDates,
      });
      if (file) await api.sources.upload(project.id, file);
      if (sourceText.trim()) await api.sources.addText(project.id, sourceText.trim(), sourceName.trim() || "프로젝트 메모.md");
      router.push(`/projects/${project.id}/today`);
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }

  return (
    <main className="page-shell py-10 sm:py-14">
      <div className="mx-auto max-w-4xl">
        <Link href="/projects" className="mb-5 inline-block text-xs font-bold text-[#60726c] hover:text-[#166a58]">← 프로젝트 목록</Link>
        <div className="mb-8"><p className="eyebrow">New project</p><h1 className="mt-2 text-3xl font-black tracking-[-.04em]">프로젝트 시작하기</h1><p className="mt-2 text-sm text-[#687874]">목표와 실제 작업 리듬을 알려주시면 실행 가능한 계획의 기반을 만듭니다.</p></div>

        <ol className="mb-5 grid grid-cols-3 gap-2" aria-label="생성 단계">
          {["기본 정보", "가용시간", "자료 추가"].map((label, index) => {
            const number = index + 1; const active = number === step; const done = number < step;
            return <li key={label} className={`rounded-xl border px-3 py-3 text-center text-xs font-black ${active ? "border-[#166a58] bg-[#e7f3ee] text-[#115545]" : done ? "border-[#bdd8cd] bg-white text-[#37705f]" : "border-[#dce5e1] bg-[#f4f7f5] text-[#83908c]"}`}><span className="mr-1">{done ? "✓" : number}</span> {label}</li>;
          })}
        </ol>

        <section className="panel p-5 sm:p-8">
          {error && <div className="mb-5"><ErrorState message={error} /></div>}
          {step === 1 && (
            <div className="grid gap-5 sm:grid-cols-2">
              <div className="sm:col-span-2"><h2 className="text-xl font-black">어떤 프로젝트인가요?</h2><p className="mt-1 text-sm text-[#71807b]">일정의 기준이 될 이름과 날짜를 입력합니다.</p></div>
              <label className="sm:col-span-2"><span className="label">프로젝트 이름 *</span><input className="field" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} placeholder="예: PacePM MVP 출시" autoFocus /></label>
              <label className="sm:col-span-2"><span className="label">설명</span><textarea className="field min-h-28" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="프로젝트의 목표, 성공 기준, 중요한 배경을 적어주세요." /></label>
              <label><span className="label">시작일 *</span><input className="field" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
              <label><span className="label">목표일 *</span><input className="field" type="date" min={startDate} value={targetDate} onChange={(e) => setTargetDate(e.target.value)} /></label>
            </div>
          )}
          {step === 2 && (
            <div className="space-y-7">
              <div><h2 className="text-xl font-black">실제로 일할 수 있는 시간을 알려주세요</h2><p className="mt-1 text-sm text-[#71807b]">버퍼를 제외한 시간만 일정에 배정됩니다.</p></div>
              <fieldset><legend className="label">작업 가능 요일 *</legend><div className="flex flex-wrap gap-2">{weekdays.map((label, day) => <button key={label} type="button" aria-pressed={workDays.includes(day)} onClick={() => toggleDay(day)} className={`grid size-11 place-items-center rounded-xl border text-sm font-black ${workDays.includes(day) ? "border-[#166a58] bg-[#166a58] text-white" : "border-[#d3dfda] bg-white text-[#71807b]"}`}>{label}</button>)}</div></fieldset>
              <div className="grid gap-5 sm:grid-cols-2">
                <label><span className="label">하루 가용시간 *</span><div className="relative"><input className="field pr-14" type="number" min="0.5" max="24" step="0.5" value={capacity} onChange={(e) => setCapacity(Number(e.target.value))} /><span className="absolute right-4 top-3 text-sm text-[#71807b]">시간</span></div></label>
                <label><span className="label">버퍼 비율</span><div className="relative"><input className="field pr-12" type="number" min="0" max="99" value={buffer} onChange={(e) => setBuffer(Number(e.target.value))} /><span className="absolute right-4 top-3 text-sm text-[#71807b]">%</span></div><span className="mt-2 block text-xs text-[#71807b]">하루 {capacity}시간 중 {(capacity * (1 - buffer / 100)).toFixed(1)}시간을 기본 배정합니다.</span></label>
              </div>
              <div><span className="label">제외 날짜</span><div className="flex gap-2"><input className="field max-w-xs" type="date" value={excludedInput} onChange={(e) => setExcludedInput(e.target.value)} /><button type="button" className="btn btn-secondary" onClick={addExcluded}>추가</button></div>{excludedDates.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{excludedDates.map((date) => <button type="button" key={date} className="badge badge-neutral" onClick={() => setExcludedDates((current) => current.filter((value) => value !== date))}>{date} ×</button>)}</div>}</div>
            </div>
          )}
          {step === 3 && (
            <div className="space-y-7">
              <div><h2 className="text-xl font-black">자료가 있다면 함께 시작하세요</h2><p className="mt-1 text-sm text-[#71807b]">선택 단계입니다. 나중에 자료 보관함에서 추가해도 됩니다.</p></div>
              <label className="block cursor-pointer rounded-2xl border-2 border-dashed border-[#c9d8d2] bg-[#f7faf8] p-7 text-center hover:border-[#5f9c89]"><input type="file" className="sr-only" accept=".pdf,.docx,.xlsx,.txt,.md,.hwpx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /><span className="block text-2xl" aria-hidden>⇧</span><span className="mt-2 block font-bold">{file ? file.name : "파일을 선택하세요"}</span><span className="mt-1 block text-xs text-[#71807b]">PDF, DOCX, XLSX, TXT, MD, HWPX · 최대 20MB</span></label>
              <div className="relative text-center text-xs font-bold text-[#85928e]"><span className="relative z-10 bg-white px-3">또는 텍스트 붙여넣기</span><span className="absolute left-0 right-0 top-1/2 border-t border-[#e1e8e5]" /></div>
              <label><span className="label">자료 이름</span><input className="field" value={sourceName} onChange={(e) => setSourceName(e.target.value)} /></label>
              <label><span className="label">회의 메모, 인터뷰 내용, 요구사항</span><textarea className="field min-h-44" value={sourceText} onChange={(e) => setSourceText(e.target.value)} placeholder="텍스트를 붙여넣으세요." /></label>
            </div>
          )}

          <div className="mt-8 flex items-center justify-between border-t border-[#e2e9e6] pt-5">
            <button type="button" className="btn btn-secondary" disabled={step === 1 || busy} onClick={() => setStep((current) => current - 1)}>이전</button>
            {step < 3 ? <button type="button" className="btn btn-primary" onClick={next}>다음 단계</button> : <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void finish()}>{busy ? "프로젝트 생성 중…" : file || sourceText.trim() ? "생성하고 오늘로 이동" : "자료 없이 생성"}</button>}
          </div>
        </section>
      </div>
    </main>
  );
}
