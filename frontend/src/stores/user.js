import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/utils/api'
import { track } from '@/utils/tracker'
import { redirectToLogin } from '@/utils/authNavigation'
import { resetUiRemoteCapabilities } from '@/utils/ui-automation'

const AUTH_STORAGE_KEYS = ['access_token', 'refresh_token', 'token_expires_at', 'user']
const REFRESH_LOCK_NAME = 'testhub_token_refresh'
const EXPIRING_SOON_MS = 5 * 60 * 1000

// 取刚签发的 access token 的过期时间（毫秒），以 JWT 的 exp 为准。
// exp 是服务端时间：如果按本机时钟看新 token 一签发就已临期（本机时钟明显偏快，或有效期本身很短），
// 直接用 exp 会导致每次请求都先刷新，此时退回按本机时钟估算的 30 分钟；token 无法解析时同样兜底。
export const getTokenExpiresAt = (token) => {
  const now = Date.now()
  try {
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const expiresAt = JSON.parse(atob(payload)).exp * 1000
    if (expiresAt - now > EXPIRING_SOON_MS) return expiresAt
  } catch (e) {
    console.error('解析 access token 过期时间失败:', e)
  }
  return now + 30 * 60 * 1000
}

export const useUserStore = defineStore('user', () => {
  const user = ref(null)
  const accessToken = ref(localStorage.getItem('access_token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')
  const tokenExpiresAt = ref(parseInt(localStorage.getItem('token_expires_at') || '0'))

  // token刷新定时器
  let refreshTimer = null

  const isAuthenticated = computed(() => !!accessToken.value && !!user.value)

  // 检查token是否即将过期（5分钟内）
  // 必须是普通函数：Date.now() 不是响应式依赖，写成 computed 会一直返回首次求值的缓存结果
  const isTokenExpiringSoon = () => {
    if (!tokenExpiresAt.value) return false
    const now = Date.now()
    const timeLeft = tokenExpiresAt.value - now
    return timeLeft < EXPIRING_SOON_MS // 5分钟
  }

  // 检查token是否已过期
  const isTokenExpired = () => {
    if (!tokenExpiresAt.value) return false
    return Date.now() > tokenExpiresAt.value
  }

  // 启动自动刷新token定时器
  const startAutoRefresh = () => {
    // 清除现有定时器
    if (refreshTimer) {
      clearInterval(refreshTimer)
    }

    // 每2分钟检查一次token是否需要刷新
    refreshTimer = setInterval(async () => {
      if (refreshToken.value && isTokenExpiringSoon() && accessToken.value) {
        try {
          await refreshAccessToken()
        } catch (error) {
          console.error('自动刷新token失败:', error)
          // refresh token 被拒时已自动logout；网络或服务端故障则等下一轮重试
        }
      }
    }, 2 * 60 * 1000) // 2分钟检查一次
  }

  // 停止自动刷新定时器
  const stopAutoRefresh = () => {
    if (refreshTimer) {
      clearInterval(refreshTimer)
      refreshTimer = null
    }
  }

  const login = async (credentials) => {
    try {
      const response = await api.post('/auth/login/', credentials)

      // 保存双token
      accessToken.value = response.data.access
      refreshToken.value = response.data.refresh
      user.value = response.data.user

      // 过期时间取自 access token 的 exp
      const expiresAt = getTokenExpiresAt(accessToken.value)
      tokenExpiresAt.value = expiresAt

      // 持久化存储
      localStorage.setItem('access_token', accessToken.value)
      localStorage.setItem('refresh_token', refreshToken.value)
      localStorage.setItem('token_expires_at', expiresAt.toString())
      localStorage.setItem('user', JSON.stringify(user.value))

      // 启动自动刷新
      startAutoRefresh()

      track('login_success', {
        event_type: 'business',
        module: 'auth',
        page_path: '/login',
        success: true,
        metadata: {
          login_type: 'password'
        }
      })

      return response.data
    } catch (error) {
      throw error
    }
  }

  /* 短信登录暂时停用，保留原状态保存逻辑。
  const smsLogin = async (data) => {
    const response = await api.post('/auth/sms-login/', data)
    accessToken.value = response.data.access
    refreshToken.value = response.data.refresh
    user.value = response.data.user

    const expiresAt = getTokenExpiresAt(accessToken.value)
    tokenExpiresAt.value = expiresAt
    localStorage.setItem('access_token', accessToken.value)
    localStorage.setItem('refresh_token', refreshToken.value)
    localStorage.setItem('token_expires_at', expiresAt.toString())
    localStorage.setItem('user', JSON.stringify(user.value))
    startAutoRefresh()

    track('login_success', {
      event_type: 'business',
      module: 'auth',
      page_path: '/login',
      success: true,
      metadata: { login_type: 'sms' }
    })
    return response.data
  }
  */

  const register = async (userData) => {
    try {
      const response = await api.post('/auth/register/', userData)

      // 注册成功自动登录
      accessToken.value = response.data.access
      refreshToken.value = response.data.refresh
      user.value = response.data.user

      const expiresAt = getTokenExpiresAt(accessToken.value)
      tokenExpiresAt.value = expiresAt

      localStorage.setItem('access_token', accessToken.value)
      localStorage.setItem('refresh_token', refreshToken.value)
      localStorage.setItem('token_expires_at', expiresAt.toString())
      localStorage.setItem('user', JSON.stringify(user.value))

      startAutoRefresh()

      track('register_success', {
        event_type: 'business',
        module: 'auth',
        page_path: '/register',
        success: true
      })

      return response.data
    } catch (error) {
      throw error
    }
  }

  // 添加一个标记防止logout过程中的循环调用
  let isLoggingOut = false

  const clearAuthState = () => {
    // 只清内存中的登录态，同时使远程能力缓存及未完成请求失效。
    stopAutoRefresh()
    accessToken.value = ''
    refreshToken.value = ''
    user.value = null
    tokenExpiresAt.value = 0
    resetUiRemoteCapabilities()
  }

  const clearAuth = () => {
    // 所有退出分支共用本地清理。
    clearAuthState()
    AUTH_STORAGE_KEYS.forEach(key => localStorage.removeItem(key))
  }

  // 多标签页共用 localStorage：把其他标签页写入的登录态同步到本页内存，
  // 否则本页会拿着已被对方轮换作废的 refresh token 去刷新。
  const syncFromStorage = () => {
    const storedAccess = localStorage.getItem('access_token') || ''

    if (!storedAccess) {
      // 其他标签页已退出登录。这里不能再清 localStorage，对方可能已经重新登录。
      if (accessToken.value) {
        clearAuthState()
        redirectToLogin({ keepLocation: true })
      }
      return
    }

    const wasLoggedOut = !accessToken.value
    accessToken.value = storedAccess
    refreshToken.value = localStorage.getItem('refresh_token') || ''
    tokenExpiresAt.value = parseInt(localStorage.getItem('token_expires_at') || '0')

    const savedUser = localStorage.getItem('user')
    if (savedUser && savedUser !== JSON.stringify(user.value)) {
      try {
        user.value = JSON.parse(savedUser)
      } catch (e) {
        console.error('解析用户信息失败:', e)
      }
    }

    if (wasLoggedOut) {
      startAutoRefresh()
    }
  }

  window.addEventListener('storage', (event) => {
    // key 为 null 表示 localStorage.clear()
    if (event.key === null || AUTH_STORAGE_KEYS.includes(event.key)) {
      syncFromStorage()
    }
  })

  // sessionExpired：登录态失效导致的被动登出（而不是用户主动退出），跳转登录页时保留当前地址
  const logout = async ({ sessionExpired = false } = {}) => {
    // 防止重复调用logout
    if (isLoggingOut) {
      return
    }
    isLoggingOut = true

    // 停止自动刷新定时器
    stopAutoRefresh()

    try {
      // 调用logout API将refresh token加入黑名单。
      // 该接口只凭refresh token即可作废，access token已过期时同样要调用。
      if (refreshToken.value) {
        try {
          await api.post('/auth/logout/', { refresh: refreshToken.value })
        } catch (apiError) {
          // logout API调用失败不影响本地清除操作
          console.error('Logout API调用失败:', apiError)
        }
      }
    } finally {
      // 清除所有认证信息
      clearAuth()

      // 重置标记
      isLoggingOut = false

      redirectToLogin({ keepLocation: sessionExpired })
    }
  }

  // 跨标签页互斥：同一时刻只让一个标签页提交 refresh token。
  // 非安全上下文（HTTP 且非 localhost）没有 Web Locks，直接执行，由 adoptRotatedToken 兜底。
  const withRefreshLock = (task) => (
    navigator.locks?.request ? navigator.locks.request(REFRESH_LOCK_NAME, task) : task()
  )

  // refresh token 被拒时，确认是否只是其他标签页抢先完成了轮换；是则沿用对方写入的新 token。
  const adoptRotatedToken = async (sentRefreshToken) => {
    // 没有 Web Locks 时两个标签页可能同时提交，对方的新 token 也许还没写入，稍等片刻再确认
    const attempts = navigator.locks?.request ? 1 : 10
    for (let i = 0; i < attempts; i += 1) {
      if (i > 0) {
        await new Promise(resolve => setTimeout(resolve, 150))
      }
      syncFromStorage()
      if (!refreshToken.value) return false
      if (refreshToken.value !== sentRefreshToken) return true
    }
    return false
  }

  const requestTokenRefresh = async (staleAccessToken) => {
    // 等待期间其他标签页（或本页稍早的刷新）可能已换好 token：已更新且未临期就直接复用
    syncFromStorage()
    if (accessToken.value && accessToken.value !== staleAccessToken && !isTokenExpiringSoon()) {
      return accessToken.value
    }

    const sentRefreshToken = refreshToken.value
    try {
      const response = await api.post('/auth/token/refresh/', {
        refresh: sentRefreshToken
      })

      // 更新access token和过期时间
      accessToken.value = response.data.access
      const expiresAt = getTokenExpiresAt(accessToken.value)
      tokenExpiresAt.value = expiresAt

      // 如果返回了新的refresh token（启用了ROTATE_REFRESH_TOKENS）
      if (response.data.refresh) {
        refreshToken.value = response.data.refresh
        localStorage.setItem('refresh_token', refreshToken.value)
      }

      // 持久化存储
      localStorage.setItem('access_token', accessToken.value)
      localStorage.setItem('token_expires_at', expiresAt.toString())

      return response.data.access
    } catch (error) {
      const status = error.response?.status
      if (status === 401 && await adoptRotatedToken(sentRefreshToken)) {
        return accessToken.value
      }

      // 只有后端明确拒绝 refresh token（400/401）才清除认证信息并登出。
      // 网络错误、超时、5xx 时 refresh token 仍然有效，保留登录态，由定时器或下一次请求重试。
      console.error('Token refresh failed:', error)
      if (status === 400 || status === 401) {
        await logout({ sessionExpired: true })
      }
      throw error
    }
  }

  // 刷新access token。本页所有调用方共用同一个进行中的刷新，避免同一个 refresh token 被重复提交。
  // staleAccessToken：刚被后端拒绝（401）的 access token，用于判断 token 是否已在别处换新。
  let refreshPromise = null

  const refreshAccessToken = (staleAccessToken) => {
    if (!refreshPromise) {
      refreshPromise = withRefreshLock(() => requestTokenRefresh(staleAccessToken)).finally(() => {
        refreshPromise = null
      })
    }
    return refreshPromise
  }

  const fetchProfile = async () => {
    try {
      const response = await api.get('/auth/profile/')
      user.value = response.data
      localStorage.setItem('user', JSON.stringify(user.value))
      return response.data
    } catch (error) {
      if (error.response?.status === 401) {
        await logout({ sessionExpired: true })
      }
      throw error
    }
  }

  const initAuth = async () => {
    // 从 localStorage 恢复用户信息
    if (!user.value) {
      const savedUser = localStorage.getItem('user')
      if (savedUser) {
        try {
          user.value = JSON.parse(savedUser)
        } catch (e) {
          console.error('解析用户信息失败:', e)
        }
      }
    }

    if (accessToken.value) {
      // 检查 token 是否过期，过期则刷新
      if (isTokenExpired() && refreshToken.value) {
        try {
          await refreshAccessToken()
        } catch (error) {
          console.error('Token刷新失败:', error)
          // 已被登出则结束；只是网络或服务端故障时保留登录态，继续启动定时器等待重试
          if (!accessToken.value) return
        }
      }

      // 获取用户信息
      if (!user.value) {
        try {
          await fetchProfile()
        } catch (error) {
          // 401 时 fetchProfile 已自动logout；网络或服务端故障保留登录态，下次进入页面时再获取
          console.error('获取用户信息失败:', error)
          if (!accessToken.value) return
        }
      }

      startAutoRefresh()
    }
  }

  return {
    user,
    accessToken,
    refreshToken,
    tokenExpiresAt,
    isAuthenticated,
    isTokenExpiringSoon,
    isTokenExpired,
    login,
    // smsLogin,
    register,
    clearAuth,
    logout,
    refreshAccessToken,
    fetchProfile,
    initAuth,
    startAutoRefresh,
    stopAutoRefresh
  }
})
