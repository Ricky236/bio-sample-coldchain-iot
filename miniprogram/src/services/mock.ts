import type { AlarmEvent, ContractMeta, PagedResult, Task, Telemetry, TraceReport } from '@/types/api'

const wait = () => new Promise((resolve) => setTimeout(resolve, 180))
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
let task: Task = {
  task_id: 'TASK-001', device_id: 'CLD-001', sample_name: '生物样本转运箱 A', sender: '高校实验室',
  receiver: '医院检验科', carrier: '演示人员', status: 'pending_handoff', started_at: null,
  signed_at: null, rejected_at: null, rejection_reason: null, updated_at: '2026-07-13T10:00:00+08:00',
}
const telemetry: Telemetry = {
  id: 1, device_id: 'CLD-001', task_id: 'TASK-001', temperature: 4.2, humidity: 62.5, light_raw: 120,
  box_status: 'BOX_CLOSED', move_status: 'STABLE', temp_status: 'TEMP_OK', acc_total: 9.81,
  motion_score: 0.2, event_type: 'NORMAL', timestamp: '2026-07-13T10:01:00+08:00', created_at: '2026-07-13T10:01:00+08:00',
}

const history: Telemetry[] = Array.from({ length: 18 }, (_, index) => {
  const minutesAgo = (17 - index) * 5
  const temperature = Number((4.1 + Math.sin(index / 2.5) * .55 + (index === 12 ? 5.1 : 0)).toFixed(1))
  const abnormal = temperature > 8
  return {
    ...telemetry,
    id: index + 1,
    temperature,
    humidity: Number((61 + Math.cos(index / 3) * 3).toFixed(1)),
    temp_status: abnormal ? 'TEMP_ALERT' : 'TEMP_OK',
    event_type: abnormal ? 'TEMP_ALERT' : 'NORMAL',
    move_status: index === 8 ? 'IMPACT' : 'STABLE',
    timestamp: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
    created_at: new Date(Date.now() - minutesAgo * 60_000 + 3_000).toISOString(),
  }
})

const alarms: AlarmEvent[] = [
  {
    id: 3, data_id: 13, task_id: 'TASK-001', device_id: 'CLD-001', event_type: 'TEMP_ALERT',
    event_name: '温度异常', event_detail: '箱内温度短时超过 8℃，请结合持续时间复核',
    timestamp: history[12].timestamp, created_at: history[12].created_at,
  },
  {
    id: 2, data_id: 9, task_id: 'TASK-001', device_id: 'CLD-001', event_type: 'IMPACT',
    event_name: '运输碰撞', event_detail: '检测到一次明显冲击，建议检查箱体与封签',
    timestamp: history[8].timestamp, created_at: history[8].created_at,
  },
  {
    id: 1, data_id: 6, task_id: 'TASK-001', device_id: 'CLD-001', event_type: 'BOX_OPEN',
    event_name: '箱体开启', event_detail: '运输过程中检测到箱盖开启事件',
    timestamp: history[5].timestamp, created_at: history[5].created_at,
  },
]

export async function mockGetTask(taskId: string) { await wait(); if (taskId !== task.task_id) throw new Error('task not found'); return clone(task) }
export async function mockLatest(taskId: string) { await mockGetTask(taskId); return clone(history[history.length - 1]) }
export async function mockHistory(taskId: string, limit = 100): Promise<PagedResult<Telemetry>> {
  await mockGetTask(taskId); return { limit, items: clone(history.slice(-limit).reverse()) }
}
export async function mockAlarms(taskId: string, limit = 100): Promise<PagedResult<AlarmEvent>> {
  await mockGetTask(taskId); return { limit, items: clone(alarms.slice(0, limit)) }
}
export async function mockStart(taskId: string) {
  await mockGetTask(taskId)
  if (!['pending_pack', 'pending_handoff'].includes(task.status)) throw new Error('任务当前状态不允许发出')
  const now = new Date().toISOString(); task = { ...task, status: 'in_transit', started_at: now, updated_at: now }; return clone(task)
}
export async function mockSign(taskId: string) {
  await mockGetTask(taskId)
  if (!['in_transit', 'arrived'].includes(task.status)) throw new Error('当前状态不允许签收')
  const now = new Date().toISOString()
  task = { ...task, status: 'signed', signed_at: now, updated_at: now }
  return clone(task)
}
export async function mockReject(taskId: string, reason: string) {
  await mockGetTask(taskId)
  if (!reason.trim()) throw new Error('请填写拒收原因')
  if (!['in_transit', 'arrived'].includes(task.status)) throw new Error('当前状态不允许拒收')
  const now = new Date().toISOString()
  task = { ...task, status: 'rejected', rejected_at: now, rejection_reason: reason.trim(), updated_at: now }
  return clone(task)
}
export async function mockReport(taskId: string): Promise<TraceReport> {
  const current = await mockGetTask(taskId)
  const temperatures = history.map((item) => item.temperature)
  const humidity = history.map((item) => item.humidity)
  const handoffNodes: TraceReport['handoff_nodes'] = []
  if (current.started_at) handoffNodes.push({ type: 'started', timestamp: current.started_at })
  if (current.signed_at) handoffNodes.push({ type: 'signed', timestamp: current.signed_at })
  if (current.rejected_at) handoffNodes.push({ type: 'rejected', timestamp: current.rejected_at, reason: current.rejection_reason })
  return {
    task: current,
    latest: clone(history[history.length - 1]),
    summary: {
      total_records: history.length,
      min_temperature: Math.min(...temperatures), max_temperature: Math.max(...temperatures),
      avg_temperature: Number((temperatures.reduce((sum, value) => sum + value, 0) / temperatures.length).toFixed(2)),
      min_humidity: Math.min(...humidity), max_humidity: Math.max(...humidity), event_count: alarms.length,
    },
    events: clone(alarms), handoff_nodes: handoffNodes,
  }
}
export const mockContracts = async (): Promise<ContractMeta> => ({
  task_statuses: ['pending_pack', 'pending_handoff', 'in_transit', 'arrived', 'signed', 'rejected', 'canceled'],
  box_statuses: ['BOX_OPEN', 'BOX_CLOSED'], move_statuses: ['STABLE', 'MILD', 'SEVERE', 'IMPACT', 'FREE_FALL'],
  temperature_statuses: ['TEMP_OK', 'TEMP_ALERT'], timestamp_format: 'ISO 8601', field_naming: 'snake_case',
})
