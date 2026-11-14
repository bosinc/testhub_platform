let loginRedirectPending = false

export const redirectToLogin = () => {
  // 登录页保留原页面，并发鉴权失败也只触发一次整页跳转。
  if (window.location.pathname.replace(/\/+$/, '') === '/login' || loginRedirectPending) return
  loginRedirectPending = true
  window.location.replace('/login')
}
