"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, type AiProviderSetting } from "@/lib/api";

const PROVIDER_HELP: Record<string, { placeholder: string; hint: string }> = {
  anthropic: { placeholder: "sk-ant-…", hint: "console.anthropic.com → API Keys" },
  openai: { placeholder: "sk-proj-…", hint: "platform.openai.com → API Keys" },
  gemini: { placeholder: "AIza…", hint: "aistudio.google.com → Get API key" },
};

function ProviderCard({
  entry,
  onChanged,
}: {
  entry: AiProviderSetting;
  onChanged: (providers: AiProviderSetting[]) => void;
}) {
  const [keyInput, setKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const help = PROVIDER_HELP[entry.provider] ?? { placeholder: "API key", hint: "" };

  const save = async () => {
    if (!keyInput.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.aiSettings.saveKey(entry.provider, keyInput.trim());
      onChanged(result.providers);
      setKeyInput("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.aiSettings.deleteKey(entry.provider);
      onChanged(result.providers);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="card p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-extrabold">{entry.label}</h3>
          <p className="mt-1 text-[12px] text-[#72817d]">모델: {entry.model}</p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${
            entry.configured ? "bg-[#e3efe9] text-[#166a58]" : "bg-[#f1f0ec] text-[#8a938f]"
          }`}
        >
          {entry.configured ? "연동됨" : "미설정"}
        </span>
      </div>

      {entry.configured && (
        <p className="mt-3 text-[12px] text-[#4d5a55]">
          현재 키: <code className="rounded bg-[#f4f6f5] px-1.5 py-0.5">{entry.masked_key}</code>
          <span className="ml-2 text-[#8a938f]">
            {entry.source === "ui" ? "설정 화면에서 저장됨" : "서버 환경변수(.env)"}
          </span>
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <input
          className="field flex-1"
          type="password"
          autoComplete="off"
          placeholder={help.placeholder}
          value={keyInput}
          onChange={(event) => setKeyInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void save();
          }}
        />
        <button className="btn btn-primary btn-sm" onClick={() => void save()} disabled={busy || !keyInput.trim()}>
          저장
        </button>
        {entry.source === "ui" && (
          <button className="btn btn-ghost btn-sm text-[#b3403a]" onClick={() => void remove()} disabled={busy}>
            삭제
          </button>
        )}
      </div>
      <p className="mt-2 text-[11px] text-[#8a938f]">발급: {help.hint}</p>
      {error && <p className="mt-2 text-[12px] font-semibold text-[#b3403a]">{error}</p>}
    </article>
  );
}

export default function GlobalSettingsPage() {
  const [providers, setProviders] = useState<AiProviderSetting[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.aiSettings.get();
      setProviders(result.providers);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="page-shell py-10">
      <p className="text-[11px] font-black uppercase tracking-[.14em] text-[#166a58]">Workspace Settings</p>
      <h1 className="mt-1 text-[26px] font-black tracking-[-.02em]">환경 설정</h1>
      <p className="mt-1 text-[13px] text-[#72817d]">
        AI 분석에 사용할 API 키를 연동합니다. 키는 이 컴퓨터의 MyPM 데이터베이스에만 저장되고, 화면에는 마스킹된
        값만 표시됩니다.
      </p>

      <section className="mt-8">
        <h2 className="text-[15px] font-extrabold">AI API 연동</h2>
        <p className="mt-1 text-[12px] text-[#72817d]">
          여러 개를 연동하면 Claude → OpenAI → Gemini 순서로 시도하고, 실패하면 다음 제공자로 자동 전환합니다. 키가
          하나도 없으면 내장 Mock 분석기로 동작합니다.
        </p>
        {loadError && <p className="mt-4 text-[13px] font-semibold text-[#b3403a]">{loadError}</p>}
        {!providers && !loadError && <p className="mt-4 text-[13px] text-[#72817d]">불러오는 중…</p>}
        {providers && (
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            {providers.map((entry) => (
              <ProviderCard key={entry.provider} entry={entry} onChanged={setProviders} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
