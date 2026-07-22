import { appConfig } from '@/config/env'
import { request } from './request'
import type { AuthSession, AuthUser, LoginInput, RegisterInput } from '@/types/api'

export const AUTH_SESSION_KEY = 'coldchain_auth_session_v2'
const MOCK_ACCOUNTS_KEY = 'coldchain_mock_accounts_v2'
interface MockAccount extends AuthUser { password_hash: string }

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
  register: (input: RegisterInput) => appConfig.useMock ? mockRegister(input) : request<AuthSession>('/api/v1/auth/register', { method: 'POST', data: input }),
  login: (input: LoginInput) => appConfig.useMock ? mockLogin(input) : request<AuthSession>('/api/v1/auth/login', { method: 'POST', data: input }),
  me: () => request<AuthUser>('/api/v1/auth/me', { showLoading: false }),
  logout: () => appConfig.useMock ? Promise.resolve() : request<null>('/api/v1/auth/logout', { method: 'POST', showLoading: false }),
}
