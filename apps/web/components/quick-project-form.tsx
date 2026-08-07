"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorState } from "@/components/feedback";
import { api, errorMessage } from "@/lib/api";

export function QuickProjectForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const project = await api.projects.create({ name: name.trim(), owner: owner.trim() || null });
      router.push(`/projects/${project.id}/today`);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="panel mx-auto max-w-xl p-5 sm:p-8">
      {error && <div className="mb-5"><ErrorState message={error} /></div>}
      <div className="space-y-5">
        <label><span className="label">프로젝트 이름</span><input className="field" value={name} onChange={(event) => setName(event.target.value)} maxLength={255} placeholder="예: MyPM 베타 출시" autoFocus /></label>
        <label><span className="label">담당자 <span className="font-normal text-[#82908c]">(선택)</span></span><input className="field" value={owner} onChange={(event) => setOwner(event.target.value)} maxLength={100} placeholder="예: 이정현" /></label>
      </div>
      <button className="btn btn-primary mt-7 w-full" disabled={busy || !name.trim()}>{busy ? "프로젝트 생성 중…" : "프로젝트 만들기"}</button>
    </form>
  );
}
