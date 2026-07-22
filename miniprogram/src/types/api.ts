export type TaskStatus = 'pending_pack' | 'pending_handoff' | 'in_transit' | 'arrived' | 'signed' | 'rejected' | 'canceled'
export type BoxStatus = 'BOX_OPEN' | 'BOX_CLOSED'
export type MoveStatus = 'STABLE' | 'MILD' | 'SEVERE' | 'IMPACT' | 'FREE_FALL'
export type TemperatureStatus = 'TEMP_OK' | 'TEMP_ALERT'
export type UserRole = 'admin' | 'sender' | 'carrier' | 'receiver'

export interface AuthUser {
  id: string; name: string; phone: string; organization: string; role: UserRole; created_at: string
}
export interface AuthSession { token: string; user: AuthUser }
export interface LoginInput { phone: string; password: string }
export interface RegisterInput {
  name: string; phone: string; organization: string; role: Exclude<UserRole, 'admin'>; password: string
}

export interface ApiResponse<T> { code: number; message: string; data: T }
export interface Task {
  task_id: string; device_id: string; sample_name: string; sender: string; receiver: string; carrier: string
  status: TaskStatus; started_at: string | null; signed_at: string | null; rejected_at: string | null
  rejection_reason: string | null; updated_at: string; owner_user_id?: string | null; batch?: string | null
  expected_arrival?: string | null; box_id?: string | null; seal_id?: string | null
  temperature_range?: string | null; created_at?: string | null
}
export interface CreateTaskInput {
  sample_name: string; batch: string; receiver: string; carrier: string; expected_arrival: string
  device_id: string; box_id: string; seal_id: string; temperature_range: string
}
export interface Telemetry {
  id: number; device_id: string; task_id: string; temperature: number; humidity: number; light_raw: number
  box_status: BoxStatus; move_status: MoveStatus; temp_status: TemperatureStatus; acc_total: number
  motion_score: number; event_type: string; timestamp: string; created_at: string
}
export interface ContractMeta {
  task_statuses: TaskStatus[]; box_statuses: BoxStatus[]; move_statuses: MoveStatus[]
  temperature_statuses: TemperatureStatus[]; timestamp_format: string; field_naming: string
}

export interface PagedResult<T> { limit: number; items: T[] }

export interface AlarmEvent {
  id: number; data_id: number; task_id: string; device_id: string; event_type: string
  event_name: string; event_detail: string; timestamp: string; created_at: string
}

export interface TraceSummary {
  total_records: number; min_temperature: number | null; max_temperature: number | null
  avg_temperature: number | null; min_humidity: number | null; max_humidity: number | null
  event_count: number
}

export interface HandoffNode {
  type: 'started' | 'signed' | 'rejected'; timestamp: string; reason?: string | null
}

export interface TraceReport {
  task: Task; latest: Telemetry | null; summary: TraceSummary
  events: AlarmEvent[]; handoff_nodes: HandoffNode[]
}
