const normalizeBaseUrl = (value: string) => value.replace(/\/$/, '')
const apiBaseUrl = normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8020')

export const appConfig = {
  apiBaseUrl,
  localSimulation: import.meta.env.VITE_ALLOW_LOCAL_SIMULATION === 'true'
    || /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(apiBaseUrl),
  useMock: import.meta.env.VITE_USE_MOCK === 'true',
  demoTaskIds: (import.meta.env.VITE_DEMO_TASK_IDS || 'TASK-001').split(',').map((id) => id.trim()).filter(Boolean),
}
