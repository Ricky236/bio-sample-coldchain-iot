import { appConfig } from '@/config/env'
import type { AlarmEvent, ContractMeta, CreateTaskInput, DeviceBindingCheck, DevicePrecheck, FaceVerification, HandoffQr, HandoffSession, HardwareSnapshot, PagedResult, Task, Telemetry, TraceReport } from '@/types/api'
import {
  mockAlarms, mockContracts, mockGetTask, mockHistory, mockLatest, mockReject,
  mockReport, mockSign, mockStart,
} from './mock'
import { ApiError, request } from './request'

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
    const result = await request<PagedResult<Task> & { page?: number; page_size?: number; total?: number }>('/api/v1/tasks')
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

export const taskService = {
  listTasks: () => appConfig.useMock ? Promise.all(appConfig.demoTaskIds.map(mockGetTask)) : listBackendTasks(),
  createTask: async (input: CreateTaskInput) => {
    const [temperature_min, temperature_max] = temperatureLimits(input.temperature_range)
    const task = await request<Task>('/api/v1/tasks', {
      method: 'POST',
      data: { ...input, temperature_min, temperature_max },
    })
    return normalizeTask(task)
  },
  updateTask: async (taskId: string, input: CreateTaskInput) => {
    const [temperature_min, temperature_max] = temperatureLimits(input.temperature_range)
    const task = await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {
      method: 'PUT',
      data: { ...input, temperature_min, temperature_max },
    })
    return normalizeTask(task)
  },
  bindDevice: (deviceId: string, taskId: string) => request<unknown>(`/api/v1/devices/${encodeURIComponent(deviceId)}/bind`, { method: 'POST', data: { task_id: taskId } }),
  saveTaskPrecheck: (taskId: string, input: { passed: boolean; temperature: number | null; seal_ok: boolean; note: string }) => request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/precheck`, { method: 'POST', data: input }),
  precheckDevice: (deviceId: string, minTemp: number, maxTemp: number, allowLocal = false) => request<DevicePrecheck>(`/api/v1/devices/${encodeURIComponent(deviceId)}/precheck?min_temp=${minTemp}&max_temp=${maxTemp}&allow_local=${allowLocal ? 'true' : 'false'}`),
  checkDeviceBinding: (deviceId: string, boxId: string, sealId: string) => request<DeviceBindingCheck>(`/api/v1/devices/${encodeURIComponent(deviceId)}/bind-check`, { method: 'POST', data: { box_id: boxId, seal_id: sealId } }),
  simulateLocalReading: (deviceId: string, temperature: number, humidity = 60, taskId?: string) => request<Telemetry>(`/api/v1/devices/${encodeURIComponent(deviceId)}/simulate-reading`, { method: 'POST', data: { temperature, humidity, task_id: taskId || null } }),
  createHandoffQr: async (taskId: string) => {
    const task = normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`))
    if (!task.carrier_user_id) throw new ApiError('未找到承运人账号，请在创建运单时填写已注册承运人的手机号或姓名', 422, 42206)
    const handoff = await request<HandoffSession>(`/api/v1/tasks/${encodeURIComponent(taskId)}/handoffs`, {
      method: 'POST',
      data: { handoff_type: 'sender_to_carrier', to_user_id: Number(task.carrier_user_id) },
      showLoading: false,
    })
    return request<HandoffQr>(`/api/v1/tasks/${encodeURIComponent(taskId)}/qr-tokens`, {
      method: 'POST',
      data: { action: 'handoff_send', handoff_id: handoff.handoff_id, ttl_seconds: 60 },
      showLoading: false,
    })
  },
  verifyHandoffQr: async (token: string) => {
    const result = await request<{ handoff_id: string }>('/api/v1/qr-tokens/verify', { method: 'POST', data: { token } })
    return request<HandoffSession>(`/api/v1/handoffs/${encodeURIComponent(result.handoff_id)}`, { showLoading: false })
  },
  getHandoff: (handoffId: string) => request<HandoffSession>(`/api/v1/handoffs/${encodeURIComponent(handoffId)}`, { showLoading: false }),
  verifyFace: (handoffId: string, imageBase64: string) => request<FaceVerification>('/api/v1/face/verify', { method: 'POST', data: { handoff_id: handoffId, image_base64: imageBase64 } }),
  simulateLocalFace: (handoffId: string) => request<FaceVerification>('/api/v1/face/simulate-verify', { method: 'POST', data: { handoff_id: handoffId } }),
  confirmHandoff: async (handoffId: string, note: string) => {
    const handoff = await request<HandoffSession>(`/api/v1/handoffs/${encodeURIComponent(handoffId)}/confirm`, { method: 'POST', data: { note: note.trim() || null } })
    const task = normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(handoff.task_id)}`))
    return { task, handoff_id: handoffId, confirmed_at: handoff.confirmed_at || '' }
  },
  getContracts: () => appConfig.useMock ? mockContracts() : request<ContractMeta>('/api/v1/meta/contracts'),
  getTask: async (taskId: string) => appConfig.useMock ? mockGetTask(taskId) : normalizeTask(await request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`)),
  getLatestTelemetry: (taskId: string) => appConfig.useMock ? mockLatest(taskId) : request<Telemetry | null>(`/api/v1/tasks/${encodeURIComponent(taskId)}/telemetry/latest`),
  getHardwareSnapshot: (taskId: string) => request<HardwareSnapshot>(`/api/v1/tasks/${encodeURIComponent(taskId)}/hardware/snapshot`, { showLoading: false }),
  getTelemetryHistory: (taskId: string, limit = 100) => appConfig.useMock ? mockHistory(taskId, limit) : request<PagedResult<Telemetry>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/telemetry/history?limit=${limit}`),
  getAlarms: (taskId: string, limit = 100) => appConfig.useMock ? mockAlarms(taskId, limit) : request<PagedResult<AlarmEvent>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/alarms?limit=${limit}`),
  getTraceReport: (taskId: string) => appConfig.useMock ? mockReport(taskId) : request<TraceReport>(`/api/v1/tasks/${encodeURIComponent(taskId)}/trace-report`),
  startTask: (taskId: string) => appConfig.useMock ? mockStart(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/start`, { method: 'POST' }),
  signTask: (taskId: string) => appConfig.useMock ? mockSign(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/sign`, { method: 'POST' }),
  rejectTask: (taskId: string, reason: string) => appConfig.useMock ? mockReject(taskId, reason) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/reject`, { method: 'POST', data: { reason } }),
}
