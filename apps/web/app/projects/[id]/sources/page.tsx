"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AnalysisStatus, Project, SourceDocument } from "@mypm/shared-types";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";
import { ProjectNav } from "@/components/project-nav";
import { api, errorMessage } from "@/lib/api";

const sourceStatus: Record<string, { label: string; tone: string }> = {
  uploaded: { label: "업로드됨", tone: "badge-neutral" },
  extracting: { label: "텍스트 추출 중", tone: "badge-info" },
  text_extracted: { label: "업로드됨", tone: "badge-success" },
  analyzing: { label: "AI 분석 중", tone: "badge-info" },
  review_required: { label: "검토 필요", tone: "badge-warning" },
  completed: { label: "분석 완료", tone: "badge-success" },
  failed: { label: "분석 실패", tone: "badge-danger" },
};

export default function SourcesPage() {
  const params = useParams<{ id: string }>(); const projectId = Number(params.id);
  const [project, setProject] = useState<Project | null>(null);
  const [sources, setSources] = useState<SourceDocument[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisStatus>({ status: "not_started" });
  const [files, setFiles] = useState<File[]>([]);
  const [textName, setTextName] = useState("직접 입력.md");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setError("");
    try {
      const [projectData, sourceData, analysisData] = await Promise.all([
        api.projects.get(projectId), api.sources.list(projectId), api.analysis.status(projectId),
      ]);
      setProject(projectData); setSources(sourceData); setAnalysis(analysisData);
    } catch (e) { if (!quiet) setError(errorMessage(e)); }
    finally { if (!quiet) setLoading(false); }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!["queued", "running"].includes(analysis.status)) return;
    const timer = window.setInterval(() => void load(true), 2000);
    return () => window.clearInterval(timer);
  }, [analysis.status, load]);

  function selectFiles(items: FileList | null) {
    if (!items) return;
    const accepted = Array.from(items);
    setFiles((current) => [...current, ...accepted.filter((file) => !current.some((item) => item.name === file.name && item.size === file.size))]);
  }

  async function uploadFiles() {
    if (!files.length) return;
    setBusy(true); setError(""); setNotice("");
    try {
      for (const file of files) await api.sources.upload(projectId, file);
      setFiles([]); setNotice("자료 업로드와 텍스트 추출을 완료했습니다."); await load(true);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function addText(event: React.FormEvent) {
    event.preventDefault(); if (!text.trim()) return;
    setBusy(true); setError(""); setNotice("");
    try { await api.sources.addText(projectId, text.trim(), textName.trim() || "직접 입력.md"); setText(""); setNotice("텍스트 자료를 추가했습니다."); await load(true); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function addUrl(event: React.FormEvent) {
    event.preventDefault(); if (!url.trim()) return;
    setBusy(true); setError(""); setNotice("");
    try { await api.sources.addUrl(projectId, url.trim()); setUrl(""); setNotice("URL 내용을 가져왔습니다."); await load(true); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function startAnalysis() {
    setBusy(true); setError(""); setNotice("");
    try { const result = await api.analysis.start(projectId); setAnalysis({ status: result.status, run_id: result.run_id }); setNotice("AI 분석을 시작했습니다. 완료될 때까지 상태를 확인합니다."); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function remove(source: SourceDocument) {
    if (!window.confirm(`“${source.file_name}” 자료를 삭제할까요?`)) return;
    setDeleting(source.id); setError("");
    try { await api.sources.remove(source.id); setSources((current) => current.filter((item) => item.id !== source.id)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setDeleting(null); }
  }

  if (loading) return <main className="page-shell py-10"><LoadingState /></main>;
  if (!project) return <main className="page-shell py-10"><ErrorState message={error || "프로젝트를 찾을 수 없습니다."} /></main>;
  const analyzable = sources.some((source) => source.analysis_status === "text_extracted" || source.analysis_status === "completed" || source.analysis_status === "review_required" || source.analysis_status === "failed");
  const analyzing = ["queued", "running"].includes(analysis.status);

  return (
    <main className="page-shell py-8 sm:py-10">
      <ProjectNav projectId={projectId} projectName={project.name} />
      <div className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><p className="eyebrow">Source library</p><h2 className="mt-2 text-3xl font-black tracking-[-.04em]">자료 보관함</h2><p className="mt-2 text-sm text-[#687874]">원문 위치를 보존해 분석 결과의 근거로 사용합니다.</p></div><button className="btn btn-primary" type="button" disabled={!analyzable || analyzing || busy} onClick={() => void startAnalysis()}>{analyzing ? "AI 분석 중…" : "✦ AI 분석 실행"}</button></div>
      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {notice && <div className="success-box mb-4">{notice}</div>}
      {analysis.status === "failed" && <div className="error-box mb-4"><b>분석에 실패했습니다.</b> {analysis.error_message || "내용을 확인한 뒤 다시 시도해 주세요."} HWP 파일이라면 HWPX 또는 PDF로 변환해 다시 업로드해 주세요.</div>}

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="panel p-5 sm:p-6">
          <h3 className="font-black">파일 업로드</h3><p className="mt-1 text-xs text-[#71807b]">모든 파일 형식 지원 (문서·이미지·기타) · 파일당 최대 20MB</p>
          <div role="button" tabIndex={0} onClick={() => fileInput.current?.click()} onKeyDown={(e) => { if (e.key === "Enter") fileInput.current?.click(); }} onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(e) => { e.preventDefault(); setDragging(false); selectFiles(e.dataTransfer.files); }} className={`mt-5 cursor-pointer rounded-2xl border-2 border-dashed p-8 text-center transition-colors ${dragging ? "border-[#166a58] bg-[#eaf5f0]" : "border-[#c9d8d2] bg-[#f7faf8] hover:border-[#5f9c89]"}`}>
            <input ref={fileInput} className="sr-only" type="file" multiple onChange={(e) => selectFiles(e.target.files)} />
            <span className="text-2xl" aria-hidden>⇧</span><p className="mt-2 font-bold">파일을 끌어다 놓거나 선택하세요</p><p className="mt-1 text-xs text-[#7a8984]">문서는 원문 위치를 보존하고, 이미지는 AI가 내용을 요약해 참고합니다.</p>
          </div>
          {files.length > 0 && <div className="mt-4 space-y-2">{files.map((file, index) => <div key={`${file.name}-${index}`} className="flex items-center justify-between rounded-lg bg-[#f1f5f3] px-3 py-2 text-xs"><span className="truncate font-bold">{file.name}</span><button type="button" onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}<button type="button" className="btn btn-primary mt-2 w-full" disabled={busy} onClick={() => void uploadFiles()}>{busy ? "업로드 중…" : `${files.length}개 파일 업로드`}</button></div>}
        </section>

        <div className="space-y-5">
          <form onSubmit={addUrl} className="panel p-5 sm:p-6">
            <h3 className="font-black">URL 가져오기</h3><p className="mt-1 text-xs text-[#71807b]">웹페이지의 텍스트와 이미지 정보를 자료로 저장합니다.</p>
            <div className="mt-4 flex gap-2"><input className="field" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/spec" aria-label="가져올 URL" /><button className="btn btn-secondary shrink-0" disabled={busy || !url.trim()}>{busy ? "가져오는 중…" : "가져오기"}</button></div>
          </form>
          <form onSubmit={addText} className="panel p-5 sm:p-6">
            <h3 className="font-black">텍스트 직접 입력</h3><p className="mt-1 text-xs text-[#71807b]">회의 메모, 인터뷰, 이메일 요구사항을 붙여넣으세요.</p>
            <label className="mt-5 block"><span className="label">자료 이름</span><input className="field" value={textName} onChange={(e) => setTextName(e.target.value)} /></label>
            <label className="mt-4 block"><span className="label">내용</span><textarea className="field min-h-36" required value={text} onChange={(e) => setText(e.target.value)} placeholder="분석할 텍스트를 입력하세요." /></label>
            <button className="btn btn-secondary mt-4 w-full" disabled={busy || !text.trim()}>텍스트 자료 추가</button>
          </form>
        </div>
      </div>

      <section className="panel mt-5 overflow-hidden">
        <div className="flex items-center justify-between border-b border-[#e2e9e6] p-5"><div><h3 className="font-black">등록된 자료</h3><p className="mt-1 text-xs text-[#71807b]">{sources.length}개 문서</p></div>{analyzing && <span className="badge badge-info">분석 상태 확인 중</span>}</div>
        {sources.length === 0 ? <EmptyState title="등록된 자료가 없습니다" description="파일을 올리거나 텍스트를 직접 입력해 프로젝트의 맥락을 추가하세요." /> : <div className="divide-y divide-[#e5ebe8]">{sources.map((source) => { const status = sourceStatus[source.analysis_status] ?? { label: source.analysis_status, tone: "badge-neutral" }; return (
          <article key={source.id} className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center">
            <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#edf3f0] text-xs font-black uppercase text-[#40675b]">{source.file_type}</div>
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="truncate font-bold">{source.file_name}</p><span className={`badge ${status.tone}`}>{status.label}</span></div><p className="mt-1 text-xs text-[#788681]">원문 블록 {source.blocks.length}개 · {new Date(source.uploaded_at).toLocaleString("ko-KR")}</p>{source.error_message && <p className="mt-2 text-xs leading-5 text-[#a33a36]">{source.error_message} {source.file_type === "hwp" ? "HWPX 또는 PDF로 변환해 다시 업로드해 주세요." : ""}</p>}</div>
            <button type="button" className="btn btn-danger btn-sm" disabled={deleting === source.id || analyzing} onClick={() => void remove(source)}>{deleting === source.id ? "삭제 중…" : "삭제"}</button>
          </article>); })}</div>}
      </section>
    </main>
  );
}
