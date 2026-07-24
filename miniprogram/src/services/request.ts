import { appConfig } from '@/config/env'
import type { ApiResponse } from '@/types/api'

export class ApiError extends Error {
  constructor(message: string, public statusCode = 0, public code = -1, public detail?: unknown) { super(message) }
}

const AUTH_SESSION_KEY = 'coldchain_auth_session_v2'

interface RequestOptions { method?: UniApp.RequestOptions['method'] | 'PATCH'; data?: UniApp.RequestOptions['data']; showLoading?: boolean }

function validationMessage(detail: unknown) {
  if (!Array.isArray(detail)) return ''
  const fieldNames: Record<string, string> = {
    sample_name: '样本名称', receiver: '收货单位', carrier: '承运人', expected_arrival: '预计送达',
    device_id: '设备编号', box_id: '箱体编号', seal_id: '封签编号', temperature_range: '温控范围', batch: '批次',
  }
  const first = detail[0] as { loc?: unknown[]; msg?: string }
  const key = String(first?.loc?.[first.loc.length - 1] || '')
  const label = fieldNames[key] || key || '表单'
  const message = first?.msg || '数据不符合要求'
  return `${label}：${message.replace('String should have at least', '至少需要').replace('characters', '个字符')}`
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const showLoading = options.showLoading !== false
  if (showLoading) uni.showLoading({ title: '加载中', mask: true })
  try {
    const response = await uni.request({
      url: `${appConfig.apiBaseUrl}${path}`,
      method: (options.method || 'GET') as UniApp.RequestOptions['method'], data: options.data, timeout: 10000,
      header: {
        'content-type': 'application/json',
        ...(uni.getStorageSync(AUTH_SESSION_KEY)?.token ? { authorization: `Bearer ${uni.getStorageSync(AUTH_SESSION_KEY).token}` } : {}),
      },
    })
    const body = response.data as ApiResponse<T> | { detail?: unknown }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      const api = body as Partial<ApiResponse<T>>
      const detail = (body as { detail?: unknown }).detail
      if (response.statusCode === 401 && !path.startsWith('/api/v1/auth/')) {
        uni.removeStorageSync(AUTH_SESSION_KEY)
        setTimeout(() => uni.reLaunch({ url: '/pages/login/index' }), 50)
      }
      throw new ApiError(api.message || validationMessage(detail) || `请求失败（${response.statusCode}）`, response.statusCode, api.code, detail)
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

export async function uploadFile<T>(
  path: string,
  filePath: string,
  formData: Record<string, string>,
): Promise<T> {
  uni.showLoading({ title: '上传中', mask: true })
  try {
    const token = uni.getStorageSync(AUTH_SESSION_KEY)?.token
    const response = await uni.uploadFile({
      url: `${appConfig.apiBaseUrl}${path}`,
      filePath,
      name: 'file',
      formData,
      timeout: 30000,
      header: token ? { authorization: `Bearer ${token}` } : {},
    })
    let body: ApiResponse<T> | { detail?: unknown }
    try { body = JSON.parse(response.data) }
    catch { throw new ApiError('服务器返回了无法解析的上传结果', response.statusCode) }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      const api = body as Partial<ApiResponse<T>>
      throw new ApiError(api.message || `上传失败（${response.statusCode}）`, response.statusCode, api.code, (body as { detail?: unknown }).detail)
    }
    const api = body as ApiResponse<T>
    if (api.code !== 0) throw new ApiError(api.message || '上传失败', response.statusCode, api.code)
    return api.data
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError('文件上传失败，请检查网络和文件格式', 0, -1, error)
  } finally { uni.hideLoading() }
}

export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '操作失败，请稍后重试' }
