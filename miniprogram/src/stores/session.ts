import { defineStore } from 'pinia'
import type { AuthSession, AuthUser } from '@/types/api'
import { AUTH_SESSION_KEY, authService } from '@/services/auth'

export const useSessionStore = defineStore('session', {
  state: () => ({ user: null as AuthUser | null, token: '', restored: false }),
  getters: { isAuthenticated: (state) => Boolean(state.user) },
  actions: {
    restore() {
      try {
        const session = uni.getStorageSync(AUTH_SESSION_KEY) as AuthSession | null
        this.user = session?.user || null; this.token = session?.token || ''
      } catch { this.user = null; this.token = '' }
      this.restored = true
    },
    setSession(session: AuthSession) {
      this.user = session.user; this.token = session.token; this.restored = true
      uni.setStorageSync(AUTH_SESSION_KEY, session)
    },
    async logout() {
      try { await authService.logout() } catch { /* 本地退出不应被网络阻塞 */ }
      this.user = null; this.token = ''; uni.removeStorageSync(AUTH_SESSION_KEY)
      uni.reLaunch({ url: '/pages/login/index' })
    },
    requireSession() {
      if (!this.restored) this.restore()
      if (!this.user) { uni.reLaunch({ url: '/pages/login/index' }); return false }
      return true
    },
  },
})
