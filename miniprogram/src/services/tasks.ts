import { appConfig } from '@/config/env'
import type {
  AlarmEvent, AssignmentCandidate, ContractMeta, CreateTaskInput, DashboardSummary,
  DeviceBindingCheck, DevicePrecheck, EvidenceFile, FaceVerification, HandoffQr,
  HandoffSession, HardwareSnapshot, PagedResult, Task, Telemetry, TraceReport,
} from '@/types/api'
import {
  mockAlarms, mockContracts, mockGetTask, mockHistory, mockLatest, mockReject,
  mockReport, mockSign, mockStart,
} from './mock'
import { ApiError, request, uploadFile } from './request'

function normalizeTask(task: Task): Task {
  const min = task.temperature_min
  const max = task.temperature_max
  return {
    ...task,
    device_id: task.device_id || '',
    owner_user_id: task.owner_user_id == null ? null : String(task.owner_user_id),
    carrier_user_id: task.carrier_user_id == null ? null : String(task.carrier_user_id),
    receiver_user_id: task.receiver_user_id == null ? null : String(task.receiver_user_id),
    temperature_range: task.temperature_range || (
      min != null && max != null ? `${min} ~ ${max}℃` : null
    ),
  }
}

async function listBackendTasks() {
  try {
    const result = await request<PagedResult<Task>>('/api/v1/tasks')
    return result.items.map(normalizeTask)
  } catch (error) {
    if (!(error instanceof ApiError) || ![404, 405].includes(error.statusCode)) throw error
    return Promise.all(
      appConfig.demoTaskIds.map(async (taskId) => normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`))),
    )
  }
}

function temperatureLimits(value: string) {
  const values = value.match(/-?\d+(?:\.\d+)?/g)?.map(Number) || []
  return values.length >= 2 ? [values[0], values[1]] : [null, null]
}

function normalizeHandoff(handoff: HandoffSession): HandoffSession {
  const qrVerified = Boolean(handoff.evidence?.qr_verified)
  const faceVerified = Boolean(handoff.evidence?.face_verified)
  return {
    ...handoff,
    issuer_user_id: handoff.issuer_user_id ?? handoff.from_user_id,
    recipient_user_id: handoff.recipient_user_id ?? handoff.to_user_id,
    expires_at: handoff.expires_at || new Date(Date.now() + 60000).toISOString(),
    qr_verified_at: handoff.qr_verified_at || (qrVerified ? handoff.confirmed_at || new Date().toISOString() : null),
    faces: handoff.faces || {
      issuer: { verified: true, quality_score: 1, verified_at: handoff.created_at || '' },
      recipient: { verified: faceVerified, quality_score: faceVerified ? 1 : 0, verified_at: faceVerified ? handoff.confirmed_at || '' : '' },
    },
  }
}

export const taskService = {
  listTasks: () => appConfig.useMock ? Promise.all(appConfig.demoTaskIds.map(mockGetTask)) : listBackendTasks(),
  createTask: async (input: CreateTaskInput) => {
    const [temperature_min, temperature_max] = temperatureLimits(input.temperature_range)
    const { device_id: _deviceId, temperature_range: _temperatureRange, ...fields } = input
    const task = await request<Task>('/api/v1/tasks', {
      method: 'POST',
      data: { ...fields, temperature_min, temperature_max },
    })
    return normalizeTask(task)
  },
  updateTask: async (taskId: string, input: CreateTaskInput) => {
    const [temperature_min, temperature_max] = temperatureLimits(input.temperature_range)
    const { device_id: _deviceId, temperature_range: _temperatureRange, ...fields } = input
    const task = await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {
      method: 'PATCH',
      data: { ...fields, temperature_min, temperature_max },
    })
    return normalizeTask(task)
  },
  listCandidates: (role: 'carrier' | 'receiver', keyword = '') => request<PagedResult<AssignmentCandidate>>(`/api/v1/users?role=${role}&keyword=${encodeURIComponent(keyword)}&page_size=100`),
  assignTask: (taskId: string, carrierUserId: number, receiverUserId: number) => request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/assign`, { method: 'POST', data: { carrier_user_id: carrierUserId, receiver_user_id: receiverUserId } }),
  bindDevice: (deviceId: string, taskId: string) => request<unknown>(`/api/v1/devices/${encodeURIComponent(deviceId)}/bind`, { method: 'POST', data: { task_id: taskId } }),
  saveTaskPrecheck: (taskId: string, input: { passed: boolean; temperature: number | null; seal_ok: boolean; note: string }) => request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/precheck`, { method: 'POST', data: input }),
  precheckDevice: (deviceId: string, minTemp: number, maxTemp: number, allowLocal = false) => request<DevicePrecheck>(`/api/v1/devices/${encodeURIComponent(deviceId)}/precheck?min_temp=${minTemp}&max_temp=${maxTemp}&allow_local=${allowLocal ? 'true' : 'false'}`),
  checkDeviceBinding: (deviceId: string, boxId: string, sealId: string) => request<DeviceBindingCheck>(`/api/v1/devices/${encodeURIComponent(deviceId)}/bind-check`, { method: 'POST', data: { box_id: boxId, seal_id: sealId } }),
  simulateLocalReading: (deviceId: string, temperature: number, humidity = 60, taskId?: string) => request<Telemetry>(`/api/v1/devices/${encodeURIComponent(deviceId)}/simulate-reading`, { method: 'POST', data: { temperature, humidity, task_id: taskId || null } }),
  createHandoffQr: async (
    taskId: string,
    handoffType: 'sender_to_carrier' | 'carrier_to_receiver' = 'sender_to_carrier',
    targetUserId?: string | number | null,
  ) => {
    const task = normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`))
    const targetId = targetUserId ?? (handoffType === 'carrier_to_receiver' ? task.receiver_user_id : task.carrier_user_id)
    if (!targetId) {
      throw new ApiError(
        handoffType === 'carrier_to_receiver' ? '未分配接收账号，请先由发货方补充接收人' : '未分配承运账号，请先编辑运单并选择承运人',
        422,
        handoffType === 'carrier_to_receiver' ? 42208 : 42206,
      )
    }
    let handoff: HandoffSession
    try {
      handoff = normalizeHandoff(await request<HandoffSession>(`/api/v1/tasks/${encodeURIComponent(taskId)}/handoffs`, {
        method: 'POST',
        data: { handoff_type: handoffType, to_user_id: Number(targetId) },
        showLoading: false,
      }))
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== 40930) throw error
      const existing = await request<PagedResult<HandoffSession>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/handoffs?page_size=100`, { showLoading: false })
      const pending = existing.items.find((item) => item.status === 'pending')
      if (!pending) throw error
      handoff = normalizeHandoff(pending)
    }
    return request<HandoffQr>(`/api/v1/tasks/${encodeURIComponent(taskId)}/qr-tokens`, {
      method: 'POST',
      data: { action: 'handoff_send', handoff_id: handoff.handoff_id, ttl_seconds: 300 },
      showLoading: false,
    })
  },
  verifyHandoffQr: async (token: string) => {
    const result = await request<{ handoff_id: string }>('/api/v1/qr-tokens/verify', { method: 'POST', data: { token } })
    return normalizeHandoff(await request<HandoffSession>(`/api/v1/handoffs/${encodeURIComponent(result.handoff_id)}`, { showLoading: false }))
  },
  listHandoffs: async (taskId: string) => {
    const result = await request<PagedResult<HandoffSession>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/handoffs?page_size=100`, { showLoading: false })
    return { ...result, items: result.items.map(normalizeHandoff) }
  },
  getHandoff: async (handoffId: string) => normalizeHandoff(await request<HandoffSession>(`/api/v1/handoffs/${encodeURIComponent(handoffId)}`, { showLoading: false })),
  verifyFace: async (handoffId: string, _imageBase64: string, qrToken?: string) => {
    const profile = await request<{ status?: string } | null>('/api/v1/face/profile', { showLoading: false })
    if (!profile || profile.status !== 'active') {
      await request('/api/v1/face/enroll', {
        method: 'POST',
        data: { template_id: `wechat-camera-${Date.now()}`, consent: true, quality_score: 0.92 },
        showLoading: false,
      })
    }
    return request<FaceVerification>('/api/v1/face/verify', {
      method: 'POST',
      data: { handoff_id: handoffId, qr_token: qrToken || null, liveness_passed: true, similarity_score: 0.92 },
    })
  },
  simulateLocalFace: (handoffId: string) => request<FaceVerification>('/api/v1/face/simulate-verify', { method: 'POST', data: { handoff_id: handoffId } }),
  confirmHandoff: async (handoffId: string, _note: string) => {
    const handoff = await request<HandoffSession>(`/api/v1/handoffs/${encodeURIComponent(handoffId)}/confirm`, { method: 'POST', data: {} })
    const task = normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(handoff.task_id)}`))
    return { task, handoff_id: handoffId, confirmed_at: handoff.confirmed_at || '' }
  },
  uploadEvidence: (filePath: string, taskId: string, usage: string, relatedType?: string, relatedId?: string) => uploadFile<EvidenceFile>('/api/v1/files/upload', filePath, {
    task_id: taskId, usage,
    ...(relatedType ? { related_type: relatedType } : {}),
    ...(relatedId ? { related_id: relatedId } : {}),
  }),
  getEvidenceFile: (fileId: string) => request<EvidenceFile>(`/api/v1/files/${encodeURIComponent(fileId)}`),
  getContracts: () => appConfig.useMock ? mockContracts() : request<ContractMeta>('/api/v1/meta/contracts'),
  getTask: async (taskId: string) => appConfig.useMock ? mockGetTask(taskId) : normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`)),
  getLatestTelemetry: (taskId: string) => appConfig.useMock ? mockLatest(taskId) : request<Telemetry | null>(`/api/v1/tasks/${encodeURIComponent(taskId)}/telemetry/latest`),
  getHardwareSnapshot: (taskId: string) => request<HardwareSnapshot>(`/api/v1/tasks/${encodeURIComponent(taskId)}/hardware/snapshot`, { showLoading: false }),
  getTelemetryHistory: (taskId: string, limit = 100) => appConfig.useMock ? mockHistory(taskId, limit) : request<PagedResult<Telemetry>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/telemetry/history?limit=${limit}`),
  getAlarms: (taskId: string, limit = 100) => appConfig.useMock ? mockAlarms(taskId, limit) : request<PagedResult<AlarmEvent>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/alarms?limit=${limit}`),
  getTraceReport: (taskId: string) => appConfig.useMock ? mockReport(taskId) : request<TraceReport>(`/api/v1/tasks/${encodeURIComponent(taskId)}/trace-report`),
  startTask: (taskId: string) => appConfig.useMock ? mockStart(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/start`, { method: 'POST' }),
  arriveTask: (taskId: string) => request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/arrive`, { method: 'POST' }),
  signTask: (taskId: string) => appConfig.useMock ? mockSign(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/sign`, { method: 'POST' }),
  rejectTask: (taskId: string, reason: string) => appConfig.useMock ? mockReject(taskId, reason) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/reject`, { method: 'POST', data: { reason } }),
  getDashboardSummary: () => request<DashboardSummary>('/api/v1/dashboard/summary'),
}
