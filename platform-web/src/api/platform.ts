/**
 * Java BFF 的统一响应外壳。
 *
 * @template T data 字段中真正的业务数据类型。
 */
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

/** Java BFF 健康检查返回的数据。 */
export interface HealthData {
  status: string
  service: string
}

/** Java BFF 转发的 Python LINK 服务健康状态。 */
export interface LinkHealthData {
  status: string
  service: string
  environment: string
}

/** 组织列表中当前页面需要使用的数据。 */
export interface Organization {
  id: number
  organizationCode: string
  organizationName: string
  status: string
  createdAt: string
  updatedAt: string
}

/** 用户列表中当前页面需要使用的数据。 */
export interface PlatformUser {
  id: number
  username: string
  displayName: string
  organizationId: number
  organizationCode: string
  organizationName: string
  status: string
  createdAt: string
  updatedAt: string
}

/**
 * 访问 Java BFF 并拆开统一响应外壳。
 *
 * 浏览器只请求相对路径 /api，开发环境由 Vite 代理到 127.0.0.1:8080。
 */
async function request<T>(path: string): Promise<T> {
  return requestWithOptions<T>(path)
}

/**
 * 发送带有可选请求配置的 Java BFF 请求。
 *
 * 创建类接口通过 options 传入 method 和 body，查询接口仍可使用上方的 request。
 */
async function requestWithOptions<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const friendlyHttpErrors: Record<number, string> = {
      400: '提交的数据格式不正确，请检查后重试。',
      404: '请求的数据不存在。',
      409: '组织编码已存在，请更换编码。',
      500: 'Java BFF 处理失败，请查看后端日志。',
    }
    let errorMessage = friendlyHttpErrors[response.status] || `Java BFF 返回 HTTP ${response.status}`

    try {
      const errorBody = (await response.json()) as {
        detail?: string
        message?: string
        error?: string
      }
      const serverMessage = errorBody.detail || errorBody.message || errorBody.error
      const genericMessages = ['Bad Request', 'Not Found', 'Conflict', 'Internal Server Error']

      if (serverMessage && !genericMessages.includes(serverMessage)) {
        errorMessage = serverMessage
      }
    } catch {
      // 某些错误响应可能没有 JSON 正文，此时保留 HTTP 状态提示。
    }

    throw new Error(errorMessage)
  }

  const result = (await response.json()) as ApiResponse<T>

  if (result.code !== 0) {
    throw new Error(result.message || 'Java BFF 返回业务错误')
  }

  return result.data
}

/** 查询 Java BFF 自定义健康检查。 */
export function getPlatformHealth(): Promise<HealthData> {
  return request<HealthData>('/api/v1/platform/health')
}

/** 通过 Java BFF 查询 Python LINK 服务健康状态。 */
export function getLinkBookingHealth(): Promise<LinkHealthData> {
  return request<LinkHealthData>('/api/v1/platform/link-booking/health')
}

/** 查询全部平台组织。 */
export function getOrganizations(): Promise<Organization[]> {
  return request<Organization[]>('/api/v1/platform/organizations')
}

/** 创建平台组织；成功时返回 Java BFF 保存后的完整组织数据。 */
export function createOrganization(input: {
  organizationCode: string
  organizationName: string
}): Promise<Organization> {
  return requestWithOptions<Organization>('/api/v1/platform/organizations', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(input),
  })
}

/** 查询全部平台用户。 */
export function getPlatformUsers(): Promise<PlatformUser[]> {
  return request<PlatformUser[]>('/api/v1/platform/users')
}
