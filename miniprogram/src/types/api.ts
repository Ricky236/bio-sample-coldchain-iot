export type TaskStatus = 'pending_pack' | 'pending_handoff' | 'in_transit' | 'arrived' | 'signed' | 'rejected' | 'canceled'
export type BoxStatus = 'BOX_OPEN' | 'BOX_CLOSED'
export type MoveStatus = 'STABLE' | 'MILD' | 'SEVERE' | 'IMPACT' | 'FREE_FALL'
export type TemperatureStatus = 'TEMP_OK' | 'TEMP_ALERT'
export type UserRole = 'admin' | 'sender' | 'carrier' | 'receiver'

export interface AuthUser {
  id?: string; user_id?: string | number; name: string; phone: string; organization: string; role: UserRole; created_at?: string
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
  carrier_user_id?: string | number | null; receiver_user_id?: string | number | null
  temperature_min?: number | null; temperature_max?: number | null
}
export interface CreateTaskInput {
  sample_name: string; batch: string; receiver: string; carrier: string; expected_arrival: string
  device_id: string; box_id: string; seal_id: string; temperature_range: string
}
export interface AssignmentCandidate {
  user_id: number; name: string; display_name: string; organization: string
  role: 'carrier' | 'receiver'; status: string
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
export interface PagedResult<T> { limit?: number; page?: number; page_size?: number; total?: number; items: T[] }

export interface AlarmEvent {
  id: number; data_id: number; task_id: string; device_id: string; event_type: string
  event_name: string; event_detail: string; timestamp: string; created_at: string
}
export interface HardwareSnapshot {
  source_url: string; generated_at: string | null
  requested_task_id: string; requested_device_id: string | null
  matched: boolean; matched_by: 'task_id' | 'device_id' | null
  latest: Telemetry | null; history: Telemetry[]; recent_alarms: AlarmEvent[]
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
export interface HandoffQr {
  handoff_id: string; token: string; expires_at: string; ttl_seconds: number
  qr_payload: string; qr_image_data_url: string | null
}
export interface HandoffFaceState { verified: boolean; quality_score: number; verified_at: string }
export interface HandoffSession {
  handoff_id: string; task_id: string; action?: string; status: string
  handoff_type?: 'sender_to_carrier' | 'carrier_to_carrier' | 'carrier_to_receiver'
  issuer_user_id?: string | number; recipient_user_id?: string | number | null
  from_user_id?: string | number; to_user_id?: string | number | null; expires_at: string
  created_at?: string; confirmed_at: string | null; note?: string | null; qr_verified_at?: string | null
  faces?: { issuer?: HandoffFaceState; recipient?: HandoffFaceState }
  from_user?: { user_id: number; name: string; organization: string; role: UserRole } | null
  to_user?: { user_id: number; name: string; organization: string; role: UserRole } | null
  evidence?: { qr_verified: boolean; face_status: string; face_verified: boolean; file_count: number }
}
export interface FaceVerification {
  verification_id: string; party?: 'issuer' | 'recipient'; verified: boolean
  face_count?: number; quality_score?: number; expires_at?: string; status?: string
}
export interface EvidenceFile {
  file_id: string; task_id: string; file_name: string; file_type: string; file_size: number
  sha256: string; usage: string; related_type?: string | null; related_id?: string | null
  download_url?: string
}
export interface DashboardSummary {
  active_tasks: number; abnormal_tasks: number; online_devices: number; offline_devices: number
  today_alarm_count: number; status_distribution: Record<string, number>
  alarm_distribution: Record<string, number>; updated_at: string
}
export interface DevicePrecheck {
  device_id: string; online: boolean; passed: boolean; temperature: number | null
  humidity: number | null; box_status: BoxStatus | null; move_status: MoveStatus | null
  reported_at: string | null; reason: string; source?: 'hardware' | 'local' | 'none'
  available_device_ids?: string[]; suggested_device_id?: string | null
  fresh?: boolean; age_seconds?: number | null
}
export interface DeviceBindingCheck {
  device_id: string; box_id: string; seal_id: string; available: boolean
  occupied_task_id: string | null; checked_at: string; message: string
}
