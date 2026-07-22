import { appConfig } from '@/config/env'
import type { AlarmEvent, ContractMeta, PagedResult, Task, Telemetry, TraceReport } from '@/types/api'
import {
  mockAlarms, mockContracts, mockGetTask, mockHistory, mockLatest, mockReject,
  mockReport, mockSign, mockStart,
} from './mock'
import { request } from './request'

export const taskService = {
  getContracts: () => appConfig.useMock ? mockContracts() : request<ContractMeta>('/api/v1/meta/contracts'),
  getTask: (taskId: string) => appConfig.useMock ? mockGetTask(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`),
  getLatestTelemetry: (taskId: string) => appConfig.useMock ? mockLatest(taskId) : request<Telemetry | null>(`/api/v1/tasks/${encodeURIComponent(taskId)}/telemetry/latest`),
  getTelemetryHistory: (taskId: string, limit = 100) => appConfig.useMock ? mockHistory(taskId, limit) : request<PagedResult<Telemetry>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/telemetry/history?limit=${limit}`),
  getAlarms: (taskId: string, limit = 100) => appConfig.useMock ? mockAlarms(taskId, limit) : request<PagedResult<AlarmEvent>>(`/api/v1/tasks/${encodeURIComponent(taskId)}/alarms?limit=${limit}`),
  getTraceReport: (taskId: string) => appConfig.useMock ? mockReport(taskId) : request<TraceReport>(`/api/v1/tasks/${encodeURIComponent(taskId)}/trace-report`),
  startTask: (taskId: string) => appConfig.useMock ? mockStart(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/start`, { method: 'POST' }),
  signTask: (taskId: string) => appConfig.useMock ? mockSign(taskId) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/sign`, { method: 'POST' }),
  rejectTask: (taskId: string, reason: string) => appConfig.useMock ? mockReject(taskId, reason) : request<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}/reject`, { method: 'POST', data: { reason } }),
  async listDemoTasks() {
    const results = await Promise.allSettled(appConfig.demoTaskIds.map((id) => this.getTask(id)))
    return results.filter((item): item is PromiseFulfilledResult<Task> => item.status === 'fulfilled').map((item) => item.value)
  },
}
