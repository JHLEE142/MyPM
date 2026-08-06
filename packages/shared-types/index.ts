export type ProjectStatus = "active" | "paused" | "completed" | string;
export type PaceStatus = "normal" | "warning" | "risk" | "critical";
export type TaskPriority = "critical" | "high" | "medium" | "low";
export type TaskCadence = "monthly" | "weekly" | "daily" | null;
export type TaskStatus =
  | "extracted"
  | "pending_review"
  | "approved"
  | "scheduled"
  | "in_progress"
  | "completed"
  | "on_hold"
  | "blocked"
  | "rejected"
  | string;

export interface Project {
  id: number;
  name: string;
  owner: string | null;
  description: string | null;
  start_date: string;
  target_date: string;
  work_days: number[];
  daily_capacity_hours: number;
  buffer_ratio: number;
  excluded_dates: string[];
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  name: string;
  owner?: string | null;
  description?: string | null;
  start_date?: string;
  target_date?: string;
  work_days?: number[];
  daily_capacity_hours?: number;
  buffer_ratio?: number;
  excluded_dates?: string[];
  status?: ProjectStatus;
}

export type ProjectPatch = Partial<ProjectCreate>;

export interface DraftTaskCandidate {
  title: string;
  estimated_hours: number;
  priority: TaskPriority;
}

export interface DraftFields {
  name?: string | null;
  description?: string | null;
  goal?: string | null;
  start_date?: string | null;
  target_date?: string | null;
  work_days?: number[] | null;
  daily_capacity_hours?: number | null;
  buffer_ratio?: number | null;
  excluded_dates?: string[] | null;
  task_candidates?: DraftTaskCandidate[] | null;
}

export interface DraftMessage {
  role: "user" | "assistant";
  content: string;
}

export interface Draft {
  id: number;
  status: "active" | "confirmed" | "discarded";
  fields: DraftFields;
  completeness_percent: number;
  conversation: DraftMessage[];
  created_at: string;
  updated_at: string;
}

export interface DraftChatResponse {
  reply: string;
  fields: DraftFields;
  completeness_percent: number;
  next_question: string | null;
}

export interface RouterProviderStatus {
  name: string;
  available: boolean;
  today_calls: number;
  quota: number | null;
  last_success_at: string | null;
}

export interface RouterStatus {
  providers: RouterProviderStatus[];
}

export interface SourceBlock {
  id: number;
  source_document_id: number;
  block_type: string;
  page_number: number | null;
  sheet_name: string | null;
  section_title: string | null;
  content: string;
  block_order: number;
  location_metadata: Record<string, unknown>;
}

export interface SourceDocument {
  id: number;
  project_id: number;
  file_name: string;
  file_type: string;
  extracted_text: string | null;
  analysis_status: string;
  error_message: string | null;
  uploaded_at: string;
  blocks: SourceBlock[];
}

export interface TaskDependency {
  task_id: number;
  depends_on_task_id: number;
  dependency_type: string;
}

export interface TaskSourceLink {
  task_id: number;
  source_block_id: number;
  relevance_score: number;
}

export interface Task {
  id: number;
  project_id: number;
  milestone_id: number | null;
  parent_task_id: number | null;
  cadence: TaskCadence;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: TaskPriority;
  estimated_hours: number;
  actual_hours: number;
  progress_percent: number;
  planned_start_date: string | null;
  planned_end_date: string | null;
  actual_start_date: string | null;
  actual_end_date: string | null;
  due_date: string | null;
  locked: boolean;
  ai_generated: boolean;
  confidence: number | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
  dependencies: TaskDependency[];
  source_links: TaskSourceLink[];
}

export interface TaskCreate {
  title: string;
  description?: string | null;
  status?: TaskStatus;
  priority?: TaskPriority;
  estimated_hours?: number;
  progress_percent?: number;
  due_date?: string | null;
  locked?: boolean;
  dependency_ids?: number[];
}

export type TaskPatch = Partial<Omit<TaskCreate, "dependency_ids">> & {
  actual_hours?: number;
  actual_start_date?: string | null;
  actual_end_date?: string | null;
  planned_start_date?: string | null;
  planned_end_date?: string | null;
  dependency_ids?: number[];
};

export interface Forecast {
  status: string;
  estimated_completion_date?: string | null;
  work_days_observed: number;
  velocity_hours_per_day: number | null;
  remaining_work_days?: number;
  remaining_hours: number;
  required_daily_hours: number | null;
  message?: string;
}

export interface Pace {
  actual_progress_percent: number;
  planned_progress_percent: number;
  pace_ratio: number | null;
  status: PaceStatus;
  delay_days: number;
  target_date: string;
  forecast: Forecast;
}

export interface TodayItem {
  task_id: number;
  date: string;
  hours: number;
  protected: boolean;
  title: string | null;
  status: TaskStatus | null;
}

export interface Dashboard {
  project_id: number;
  as_of: string;
  pace: Pace;
  today: {
    available_hours: number;
    raw_daily_capacity_hours: number;
    effective_daily_capacity_hours: number;
    assigned_hours: number;
    over_capacity: boolean;
    excess_hours: number;
    message: string;
    items: TodayItem[];
  };
  blocked_task_ids: number[];
}

export interface Placement {
  task_id: number;
  date: string;
  hours: number;
  protected: boolean;
  original_estimated_hours?: number;
  remaining_estimated_hours?: number;
}

export interface ScheduleSnapshot {
  task_ids: number[];
  placements: Placement[];
  daily_loads: Array<{
    date: string;
    assigned_hours: number;
    effective_capacity_hours: number;
  }>;
  effective_daily_capacity_hours: number;
  buffer_hours: number;
  infeasible: boolean;
  unscheduled: Array<{ task_id: number; remaining_hours: number; reason: string }>;
  warnings: string[];
  protected_task_ids?: number[];
  deferred?: Array<{ task_id: number; reason: string }>;
}

export interface Milestone {
  id: number;
  project_id: number;
  title: string;
  description: string | null;
  target_date: string | null;
  status: string;
  sort_order: number;
}

export interface ScheduleVersion {
  id?: number;
  project_id?: number;
  version: number | null;
  reason?: string;
  created_at?: string;
  schedule_snapshot: ScheduleSnapshot | null;
}

export interface ScheduleVersionSummary {
  id: number;
  project_id: number;
  version: number;
  reason: string;
  created_at: string;
}

export interface ScheduleComparison {
  from_version: number;
  to_version: number;
  changes: Array<{
    task_id: number;
    before: Array<[string, number]>;
    after: Array<[string, number]>;
  }>;
}

export interface AnalysisStatus {
  run_id?: number;
  status: "not_started" | "queued" | "running" | "completed" | "failed" | string;
  model_provider?: string;
  prompt_version?: string;
  started_at?: string;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface ProjectFact {
  id: number;
  fact_type: string;
  content: string;
  confidence: number;
  review_status: string;
  source_block_id: number | null;
}

export interface AnalysisReview {
  facts: ProjectFact[];
  tasks: Task[];
  source_blocks: SourceBlock[];
}

export type ReviewAction = "approve" | "modify" | "reject" | "hold";
export interface ReviewDecision {
  id: number;
  action: ReviewAction;
  updates?: Record<string, unknown>;
}

export interface ApprovalRequest {
  tasks: ReviewDecision[];
  facts: ReviewDecision[];
}

export interface ReplanRequest {
  reason: string;
  strategy: "redistribute" | "increase_capacity" | "defer_low_priority" | "change_target";
  daily_capacity_hours?: number;
  target_date?: string;
}
