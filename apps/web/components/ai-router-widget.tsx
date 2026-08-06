"use client";

import { useEffect, useState } from "react";
import type { RouterStatus } from "@pacepm/shared-types";
import { api } from "@/lib/api";

const providerLabels: Record<string, string> = {
  anthropic_api: "Claude API",
  openai_api: "OpenAI API",
  claude_agent: "Claude Agent",
  codex_cli: "Codex CLI",
  mock: "Mock",
};

export function AiRouterWidget() {
  const [status, setStatus] = useState<RouterStatus | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void api.router.status().then((value) => { if (active) setStatus(value); }).catch(() => { if (active) setStatus(null); });
    };
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (!status) return <span className="hidden text-[11px] font-bold text-[#87938f] lg:inline">AI 상태 확인 중</span>;

  return (
    <div className="hidden items-center gap-1.5 rounded-xl border border-[#dce5e1] bg-white/80 px-2 py-1.5 lg:flex" aria-label="AI Router 상태">
      {status.providers.map((provider) => (
        <span key={provider.name} className="flex items-center gap-1 rounded-lg px-1.5 py-1 text-[10px] font-extrabold text-[#53635e]" title={`${provider.name === "claude_agent" || provider.name === "codex_cli" ? "문서 분석에는 사용되지 않고 대화형 등록에 사용 · " : ""}${provider.last_success_at ? `마지막 성공: ${provider.last_success_at}` : "성공 기록 없음"}`}>
          <span className={`size-1.5 rounded-full ${provider.available ? "bg-[#2e9b71]" : "bg-[#b5bfbb]"}`} aria-label={provider.available ? "사용 가능" : "사용 불가"} />
          {providerLabels[provider.name] ?? provider.name} {provider.today_calls}/{provider.quota ?? "∞"}
        </span>
      ))}
    </div>
  );
}
