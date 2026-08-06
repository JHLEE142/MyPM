import Link from "next/link";
import { GuidedProjectSetup } from "@/components/guided-project-setup";
import { ManualProjectForm } from "@/components/manual-project-form";
import { QuickProjectForm } from "@/components/quick-project-form";

export default async function NewProjectPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const mode = (await searchParams).mode;
  const detailed = mode === "guided" || mode === "manual";

  return (
    <main className="page-shell py-8 sm:py-12">
      <div className={detailed ? "mx-auto max-w-6xl" : "mx-auto max-w-xl"}>
        <div className="mb-7">
          <Link href="/projects" className="mb-4 inline-block text-xs font-bold text-[#60726c] hover:text-[#166a58]">← 프로젝트 목록</Link>
          <p className="eyebrow">New project</p>
          <h1 className="mt-2 text-3xl font-black tracking-[-.04em]">프로젝트 시작하기</h1>
          <p className="mt-2 text-sm text-[#687874]">{detailed ? "프로젝트의 실행 기준을 자세히 정하세요." : "이름만 입력하면 기본 설정으로 바로 시작할 수 있습니다."}</p>
        </div>
        {mode === "guided" ? <GuidedProjectSetup /> : mode === "manual" ? <ManualProjectForm /> : <QuickProjectForm />}
        <div className="mt-5 flex flex-wrap justify-center gap-x-5 gap-y-2 text-xs font-bold text-[#687874]">
          {mode !== "guided" && <Link href="/projects/new?mode=guided" className="hover:text-[#166a58]">AI 대화로 자세히 만들기</Link>}
          {mode !== "manual" && <Link href="/projects/new?mode=manual" className="hover:text-[#166a58]">상세 폼으로 만들기</Link>}
          {detailed && <Link href="/projects/new" className="hover:text-[#166a58]">간단히 만들기</Link>}
        </div>
      </div>
    </main>
  );
}
