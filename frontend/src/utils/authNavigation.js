let loginRedirectPending = false

// keepLocation：登录态失效（而不是主动退出）时带上当前地址，重新登录后回到原页面。
export const redirectToLogin = ({ keepLocation = false } = {}) => {
  // 登录页保留原页面，并发鉴权失败也只触发一次整页跳转。
  const { pathname, search, hash } = window.location
  const path = pathname.replace(/\/+$/, '')
  if (path === '/login' || loginRedirectPending) return
  loginRedirectPending = true
  // 首页本来就是登录后的默认落地页，不需要带参数
  const remember = keepLocation && path !== '' && path !== '/home'
  window.location.replace(
    remember ? `/login?redirect=${encodeURIComponent(pathname + search + hash)}` : '/login'
  )
}

// 登录后的落地页。redirect 来自地址栏，只接受站内路径，避免被用来跳转到外部站点。
export const resolveLoginRedirect = (redirect) => {
  if (typeof redirect !== 'string' || !/^\/(?![/\\])/.test(redirect)) return '/home'
  if (/^\/login(?:[/?#]|$)/.test(redirect)) return '/home'
  return redirect
}
