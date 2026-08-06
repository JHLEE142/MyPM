"use client";

import Link from "next/link";
import { useState } from "react";
import { GuidedProjectSetup } from "@/components/guided-project-setup";
import { ManualProjectForm } from "@/components/manual-project-form";

export default function NewProjectPage() {
  const [manual, setManual] = useState(false);

  return (
    <main className="page-shell py-8 sm:py-12">
      <div className={manual ? "mx-auto max-w-4xl" : "mx-auto max-w-6xl"}>
        <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div><Link href="/projects" className="mb-4 inline-block text-xs font-bold text-[#60726c] hover:text-[#166a58]">← 프로젝트 목록</Link><p className="eyebrow">New project</p><h1 className="mt-2 text-3xl font-black tracking-[-.04em]">프로젝트 시작하기</h1><p className="mt-2 text-sm text-[#687874]">AI와 대화하거나 익숙한 폼으로 프로젝트의 실행 기준을 정하세요.</p></div>
          <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-[#d6e1dd] bg-white px-4 py-3 text-sm font-black text-[#40514c]"><span>수동 입력으로 전환</span><input type="checkbox" className="peer sr-only" checked={manual} onChange={(event) => setManual(event.target.checked)} /><span className="relative h-6 w-11 rounded-full bg-[#ccd7d3] transition peer-checked:bg-[#166a58] after:absolute after:left-1 after:top-1 after:size-4 after:rounded-full after:bg-white after:transition-transform peer-checked:after:translate-x-5" aria-hidden /></label>
        </div>
        {manual ? <ManualProjectForm /> : <GuidedProjectSetup />}
      </div>
    </main>
  );
}
