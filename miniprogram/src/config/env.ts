const normalizeBaseUrl = (value: string) => value.replace(/\/$/, '')

export const appConfig = {
  apiBaseUrl: normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8020'),
  useMock: import.meta.env.VITE_USE_MOCK === 'true',
  demoTaskIds: (import.meta.env.VITE_DEMO_TASK_IDS || 'TASK-001').split(',').map((id) => id.trim()).filter(Boolean),
}
