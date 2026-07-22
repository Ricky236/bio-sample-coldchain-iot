import { appConfig } from '@/config/env'
import type { ApiResponse } from '@/types/api'

export class ApiError extends Error {
  constructor(message: string, public statusCode = 0, public code = -1, public detail?: unknown) { super(message) }
}

interface RequestOptions { method?: UniApp.RequestOptions['method']; data?: UniApp.RequestOptions['data']; showLoading?: boolean }

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const showLoading = options.showLoading !== false
  if (showLoading) uni.showLoading({ title: '加载中', mask: true })
  try {
    const response = await uni.request({
      url: `${appConfig.apiBaseUrl}${path}`,
      method: options.method || 'GET', data: options.data, timeout: 10000,
      header: {
        'content-type': 'application/json',
        ...(uni.getStorageSync('coldchain_auth_session_v2')?.token ? { authorization: `Bearer ${uni.getStorageSync('coldchain_auth_session_v2').token}` } : {}),
      },
    })
    const body = response.data as ApiResponse<T> | { detail?: unknown }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      const api = body as Partial<ApiResponse<T>>
      throw new ApiError(api.message || `请求失败（${response.statusCode}）`, response.statusCode, api.code, (body as { detail?: unknown }).detail)
    }
    const api = body as ApiResponse<T>
    if (api.code !== 0) throw new ApiError(api.message || '业务请求失败', response.statusCode, api.code)
    return api.data
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError('网络连接失败，请检查网络后重试', 0, -1, error)
  } finally {
    if (showLoading) uni.hideLoading()
  }
}

export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '操作失败，请稍后重试' }
