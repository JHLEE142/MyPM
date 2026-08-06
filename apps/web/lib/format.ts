import type { PaceStatus, TaskPriority, TaskStatus } from "@pacepm/shared-types";

export const formatDate = (value?: string | null) => {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric", weekday: "short" }).format(
    new Date(`${value.slice(0, 10)}T00:00:00`),
  );
};

export const formatFullDate = (value?: string | null) => {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric" }).format(
    new Date(`${value.slice(0, 10)}T00:00:00`),
  );
};

export const formatHours = (hours: number) => {
  if (hours < 1 && hours > 0) return `${Math.round(hours * 60)}분`;
  return `${Number(hours.toFixed(1))}시간`;
};

export const paceLabel: Record<string, string> = {
  normal: "정상",
  on_track: "정상",
  warning: "주의",
  risk: "위험",
  critical: "심각",
};

export const paceTone = (status: PaceStatus) => {
  if (status === "normal") return "badge-success";
  if (status === "warning") return "badge-warning";
  if (status === "risk") return "badge-danger";
  return "badge-critical";
};

export const taskStatusLabel: Record<string, string> = {
  extracted: "추출됨",
  pending_review: "검토 대기",
  approved: "승인됨",
  scheduled: "예정",
  in_progress: "진행 중",
  completed: "완료",
  on_hold: "보류",
  blocked: "차단됨",
  rejected: "거절됨",
};

export const taskStatusTone = (status: TaskStatus) => {
  if (status === "completed") return "badge-success";
  if (status === "blocked") return "badge-danger";
  if (status === "in_progress") return "badge-info";
  if (status === "pending_review") return "badge-warning";
  return "badge-neutral";
};

export const priorityLabel: Record<TaskPriority, string> = {
  critical: "긴급",
  high: "높음",
  medium: "보통",
  low: "낮음",
};

export const isoToday = () => {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
};
