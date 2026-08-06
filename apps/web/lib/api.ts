import type {
  AnalysisReview,
  AnalysisStatus,
  ApprovalRequest,
  Dashboard,
  Project,
  ProjectCreate,
  ProjectPatch,
  ReplanRequest,
  ScheduleComparison,
  ScheduleVersion,
  ScheduleVersionSummary,
  SourceDocument,
  Task,
  TaskCreate,
  TaskPatch,
} from "@pacepm/shared-types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    let message = `요청에 실패했습니다. (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).filter(Boolean).join(", ");
    } catch {
      // JSON 오류 본문이 아니면 기본 메시지를 사용합니다.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (value: unknown) => JSON.stringify(value);

export const api = {
  projects: {
    list: () => request<Project[]>("/api/projects"),
    get: (id: number) => request<Project>(`/api/projects/${id}`),
    create: (payload: ProjectCreate) => request<Project>("/api/projects", { method: "POST", body: json(payload) }),
    update: (id: number, payload: ProjectPatch) =>
      request<Project>(`/api/projects/${id}`, { method: "PATCH", body: json(payload) }),
    remove: (id: number) => request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  },
  sources: {
    list: (projectId: number) => request<SourceDocument[]>(`/api/projects/${projectId}/sources`),
    upload: (projectId: number, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<SourceDocument>(`/api/projects/${projectId}/sources`, { method: "POST", body: form });
    },
    addText: (projectId: number, text: string, fileName: string) =>
      request<SourceDocument>(`/api/projects/${projectId}/sources`, {
        method: "POST",
        body: json({ text, file_name: fileName }),
      }),
    remove: (sourceId: number) => request<void>(`/api/sources/${sourceId}`, { method: "DELETE" }),
  },
  analysis: {
    start: (projectId: number) =>
      request<{ run_id: number; status: string }>(`/api/projects/${projectId}/analysis`, { method: "POST" }),
    status: (projectId: number) => request<AnalysisStatus>(`/api/projects/${projectId}/analysis/status`),
    review: (projectId: number) => request<AnalysisReview>(`/api/projects/${projectId}/analysis/review`),
    approve: (projectId: number, payload: ApprovalRequest) =>
      request<{ status: string; tasks: number; facts: number }>(`/api/projects/${projectId}/analysis/approve`, {
        method: "POST",
        body: json(payload),
      }),
  },
  tasks: {
    list: (projectId: number) => request<Task[]>(`/api/projects/${projectId}/tasks`),
    create: (projectId: number, payload: TaskCreate) =>
      request<Task>(`/api/projects/${projectId}/tasks`, { method: "POST", body: json(payload) }),
    update: (taskId: number, payload: TaskPatch) =>
      request<Task>(`/api/tasks/${taskId}`, { method: "PATCH", body: json(payload) }),
    complete: (taskId: number, payload?: { actual_hours?: number; note?: string; completed_date?: string }) =>
      request<Task>(`/api/tasks/${taskId}/complete`, { method: "POST", body: json(payload ?? {}) }),
    block: (taskId: number, reason: string) =>
      request<Task>(`/api/tasks/${taskId}/block`, { method: "POST", body: json({ reason }) }),
  },
  schedule: {
    get: (projectId: number) => request<ScheduleVersion>(`/api/projects/${projectId}/schedule`),
    generate: (projectId: number) =>
      request<ScheduleVersion>(`/api/projects/${projectId}/schedule/generate`, {
        method: "POST",
        body: json({ reason: "사용자 일정 생성" }),
      }),
    replan: (projectId: number, payload: ReplanRequest) =>
      request<ScheduleVersion>(`/api/projects/${projectId}/schedule/replan`, {
        method: "POST",
        body: json(payload),
      }),
    versions: (projectId: number) =>
      request<ScheduleVersionSummary[]>(`/api/projects/${projectId}/schedule/versions`),
    compare: (projectId: number, fromVersion: number, toVersion: number) =>
      request<ScheduleComparison>(
        `/api/projects/${projectId}/schedule/versions/compare?from_version=${fromVersion}&to_version=${toVersion}`,
      ),
  },
  dashboard: (projectId: number) => request<Dashboard>(`/api/projects/${projectId}/dashboard`),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "알 수 없는 오류가 발생했습니다.";
}
