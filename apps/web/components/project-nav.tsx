import Link from "next/link";

export function ProjectNav({ projectId, projectName }: { projectId: number; projectName?: string }) {
  const links = [
    { href: `/projects/${projectId}/today`, label: "오늘" },
    { href: `/projects/${projectId}/plan`, label: "계획 · 검토" },
    { href: `/projects/${projectId}/sources`, label: "자료" },
    { href: `/projects/${projectId}/settings`, label: "설정" },
  ];
  return (
    <div className="mb-6 flex flex-col gap-4 border-b border-[#dce5e1] pb-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <Link href="/projects" className="mb-2 inline-block text-xs font-bold text-[#60726c] hover:text-[#166a58]">← 프로젝트 목록</Link>
        <h1 className="text-2xl font-black tracking-[-.035em]">{projectName ?? "프로젝트"}</h1>
      </div>
      <nav className="flex gap-1 rounded-xl bg-[#edf2ef] p-1" aria-label="프로젝트 메뉴">
        {links.map((link) => (
          <Link key={link.href} href={link.href} className="rounded-lg px-3 py-2 text-sm font-bold text-[#53635e] hover:bg-white hover:text-[#166a58]">
            {link.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
