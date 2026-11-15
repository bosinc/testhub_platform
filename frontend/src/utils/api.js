import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { redirectToLogin } from '@/utils/authNavigation'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 正在刷新的标志
let isRefreshing = false
// 等待刷新的请求队列
let failedQueue = []

// 处理队列中的请求
const processQueue = (error, token = null) => {
  failedQueue.forEach(prom => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token)
    }
  })

  failedQueue = []
}

// 请求拦截器
api.interceptors.request.use(
  async (config) => {
    const userStore = useUserStore()

    // 检查是否是刷新token的请求
    if (config.url === '/auth/token/refresh/') {
      return config
    }

    // 如果有access token
    if (userStore.accessToken) {
      // 检查token是否即将过期（5分钟内）
      if (userStore.isTokenExpiringSoon && !userStore.isTokenExpired) {
        // 如果没有正在刷新，开始刷新
        if (!isRefreshing) {
          isRefreshing = true
          console.log('Token即将过期，开始刷新...')

          try {
            const newToken = await userStore.refreshAccessToken()
            console.log('Token刷新成功')
            processQueue(null, newToken)

            // 更新当前请求的token
            config.headers.Authorization = `Bearer ${newToken}`
          } catch (error) {
            console.error('Token刷新失败:', error)
            processQueue(error, null)
            // 刷新失败会在user store中自动logout
            return Promise.reject(error)
          } finally {
            isRefreshing = false
          }
        } else {
          // 如果正在刷新，将请求加入队列
          console.log('Token正在刷新，请求加入队列等待...')
          return new Promise((resolve, reject) => {
            failedQueue.push({ resolve, reject })
          }).then(token => {
            config.headers.Authorization = `Bearer ${token}`
            return config
          }).catch(err => {
            return Promise.reject(err)
          })
        }
      }

      // 使用Bearer token格式
      config.headers.Authorization = `Bearer ${userStore.accessToken}`
    }

    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response
  },
  async (error) => {
    const userStore = useUserStore()
    const originalRequest = error.config

    // 如果是401错误且不是刷新token的请求
    if (error.response?.status === 401 && !originalRequest._retry) {
      // 如果是logout请求失败，直接清除本地状态不再重试logout，防止死循环
      if (originalRequest.url === '/auth/logout/') {
        console.error('Logout请求401，直接清除本地状态')
        userStore.clearAuth()
        redirectToLogin()
        return Promise.reject(error)
      }

      // 如果是刷新token的请求失败
      if (originalRequest.url === '/auth/token/refresh/') {
        console.error('Refresh token失败，跳转登录页')
        await userStore.logout()
        return Promise.reject(error)
      }

      // 如果有refresh token，尝试刷新
      if (userStore.refreshToken && !isRefreshing) {
        originalRequest._retry = true
        isRefreshing = true

        try {
          console.log('收到401响应，尝试刷新token...')
          const newToken = await userStore.refreshAccessToken()
          console.log('Token刷新成功，重试原请求')
          processQueue(null, newToken)

          // 更新当前请求的token
          originalRequest.headers.Authorization = `Bearer ${newToken}`

          // 重试原请求
          return api(originalRequest)
        } catch (refreshError) {
          console.error('Token刷新失败:', refreshError)
          processQueue(refreshError, null)
          await userStore.logout()
          return Promise.reject(refreshError)
        } finally {
          isRefreshing = false
        }
      } else {
        // 没有refresh token，直接退出
        console.error('没有refresh token，跳转登录页')
        await userStore.logout()
      }

      return Promise.reject(error)
    }

    // 全局错误处理（仅处理业务代码不会覆盖的场景）
    if (error.response?.status === 401) {
      ElMessage.error('登录已过期，请重新登录')
    } else if (error.response?.status >= 500) {
      ElMessage.error('服务器错误，请稍后重试')
    }
    // data.error / data.detail 由各业务代码自行处理，避免重复提示

    return Promise.reject(error)
  }
)

/**
 * 顺序拉取全部数据，请求函数签名为 request(params)，返回 { data }，兼容原有 URL 调用。
 * 支持数组响应或 DRF 的 { results, next }；始终从第一页开始，不修改传入参数。
 * pageSize 是期望的每页大小，实际是否继续只看 next，不依赖返回条数或 count。
 * maxPages 用于阻止异常循环，达到上限仍有下一页时抛错，不返回部分结果。
 * 请求错误（包括取消请求）原样向上传递，由调用方统一处理。
 */
export async function fetchAllPages(request, params = {}, { pageSize = 1000, maxPages = 50 } = {}) {
  // URL 调用也转换为相同的请求函数，统一复用下方分页和异常处理。
  if (typeof request === 'string' && request.trim()) {
    const url = request
    request = query => api.get(url, { params: query })
  }
  if (typeof request !== 'function') {
    throw new TypeError('fetchAllPages: request 必须是请求函数')
  }
  if (!params || typeof params !== 'object' || Array.isArray(params)) {
    throw new TypeError('fetchAllPages: params 必须是参数对象')
  }
  if (!Number.isSafeInteger(pageSize) || pageSize <= 0) {
    throw new RangeError('fetchAllPages: pageSize 必须是正安全整数')
  }
  if (!Number.isSafeInteger(maxPages) || maxPages <= 0) {
    throw new RangeError('fetchAllPages: maxPages 必须是正安全整数')
  }

  const results = []
  for (let page = 1; page <= maxPages; page += 1) {
    const response = await request({ ...params, page, page_size: pageSize })
    const data = response?.data
    const fail = message => new Error(`fetchAllPages: 第 ${page} 页${message}`)

    if (Array.isArray(data)) {
      // 中途切换响应格式会丢失已累积的数据，不能将当前数组当成完整列表。
      if (page !== 1) throw fail('响应格式由分页对象变为数组')
      return data
    }
    if (!data || !Array.isArray(data.results)) {
      throw fail('响应格式无效，预期数组或 { results, next }')
    }
    if (data.next !== null && (typeof data.next !== 'string' || !data.next.trim())) {
      throw fail('缺少有效的 next 字段，预期下一页地址或 null')
    }
    if (data.next !== null) {
      if (data.results.length === 0) throw fail('为空但仍存在下一页')

      // 仅校验 next 中的页码，不直接访问该地址，后续仍通过业务 API 函数请求。
      // 必须恰好指向下一页，防止重复、回退、跳页或误用游标分页导致漏数。
      let nextPages
      try {
        nextPages = new URL(data.next, 'http://pagination.local').searchParams.getAll('page')
      } catch {
        throw fail('next 地址无效')
      }
      if (nextPages.length !== 1 || Number(nextPages[0]) !== page + 1) {
        throw fail('next 页码异常，必须指向下一页')
      }
    }

    // 避免展开超大单页数组时超出 JavaScript 函数参数数量限制。
    for (const item of data.results) results.push(item)
    if (data.next === null) return results
    if (page === maxPages) {
      throw fail(`仍有下一页，已达到 maxPages=${maxPages} 上限，请调整上限或缩小查询范围`)
    }
  }
}

export default api
