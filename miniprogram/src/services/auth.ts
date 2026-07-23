import { appConfig } from '@/config/env'
import { ApiError, request } from './request'
import type { AuthSession, AuthUser, LoginInput, RegisterInput } from '@/types/api'

export const AUTH_SESSION_KEY = 'coldchain_auth_session_v2'
const MOCK_ACCOUNTS_KEY = 'coldchain_mock_accounts_v2'
interface MockAccount extends AuthUser { password_hash: string }
interface BackendUser {
  user_id: number | string; name: string; phone: string; organization: string
  role: AuthUser['role']; created_at?: string
}
interface BackendSession { token: string; user: BackendUser }

function normalizeUser(user: BackendUser): AuthUser {
  return {
    id: String(user.user_id), name: user.name, phone: user.phone,
    organization: user.organization || '', role: user.role, created_at: user.created_at || '',
  }
}

function normalizePhone(value: string) { return value.replace(/\s/g, '') }
function mockHash(value: string) {
  let hash = 2166136261
  for (let i = 0; i < value.length; i += 1) { hash ^= value.charCodeAt(i); hash = Math.imul(hash, 16777619) }
  return (hash >>> 0).toString(16).padStart(8, '0')
}
function mockAccounts(): MockAccount[] {
  try { return uni.getStorageSync(MOCK_ACCOUNTS_KEY) || [] } catch { return [] }
}
function saveMockAccounts(accounts: MockAccount[]) { uni.setStorageSync(MOCK_ACCOUNTS_KEY, accounts) }
function publicUser(account: MockAccount): AuthUser {
  const { password_hash: _passwordHash, ...user } = account
  return user
}
async function mockRegister(input: RegisterInput): Promise<AuthSession> {
  await new Promise((resolve) => setTimeout(resolve, 250))
  const phone = normalizePhone(input.phone)
  const accounts = mockAccounts()
  if (accounts.some((item) => item.phone === phone)) throw new Error('该手机号已经注册')
  const account: MockAccount = {
    id: `U-${Date.now()}`, name: input.name.trim(), phone, organization: input.organization.trim(),
    role: input.role, password_hash: mockHash(`${phone}:${input.password}`), created_at: new Date().toISOString(),
  }
  accounts.push(account); saveMockAccounts(accounts)
  return { token: `mock-${account.id}-${Date.now()}`, user: publicUser(account) }
}
async function mockLogin(input: LoginInput): Promise<AuthSession> {
  await new Promise((resolve) => setTimeout(resolve, 250))
  const phone = normalizePhone(input.phone)
  const account = mockAccounts().find((item) => item.phone === phone)
  if (!account || account.password_hash !== mockHash(`${phone}:${input.password}`)) throw new Error('手机号或密码错误')
  return { token: `mock-${account.id}-${Date.now()}`, user: publicUser(account) }
}

export const authService = {
  register: async (input: RegisterInput) => {
    if (appConfig.useMock) return mockRegister(input)
    try {
      await request<{ user: BackendUser }>('/api/v1/auth/register', { method: 'POST', data: input })
    } catch (error) {
      if (!(error instanceof ApiError) || error.statusCode !== 409) throw error
      // 注册接口已成功但自动登录中断时，再次提交会得到 409；直接按本次密码尝试登录。
    }
    return authService.login({ phone: input.phone, password: input.password })
  },
  login: async (input: LoginInput) => {
    if (appConfig.useMock) return mockLogin(input)
    const session = await request<BackendSession>('/api/v1/auth/login', { method: 'POST', data: input })
    return { token: session.token, user: normalizeUser(session.user) }
  },
  me: async () => normalizeUser(await request<BackendUser>('/api/v1/auth/me', { showLoading: false })),
  logout: () => appConfig.useMock ? Promise.resolve() : request<null>('/api/v1/auth/logout', { method: 'POST', showLoading: false }),
}
