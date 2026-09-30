// Holds the signed-in user and their role for the whole app. Every screen reads `role`
// from here rather than decoding the token itself.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, getToken, setToken } from './api.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // On load, ask the server whether a stored token is still valid. With no token there is
  // nothing to validate — skip the call rather than provoke a guaranteed 401.
  useEffect(() => {
    if (!getToken()) {
      setLoading(false)
      return
    }

    let cancelled = false
    api('/api/auth/session')
      .then((me) => {
        if (!cancelled) setUser(me)
      })
      .catch(() => {
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email, password) => {
    const session = await api('/api/auth/login', {
      method: 'POST',
      body: { email, password },
    })
    setToken(session.access_token)
    setUser(session.user)
    return session.user
  }, [])

  const register = useCallback(async (payload) => {
    const session = await api('/api/auth/register', { method: 'POST', body: payload })
    setToken(session.access_token)
    setUser(session.user)
    return session.user
  }, [])

  const logout = useCallback(async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' })
    } catch {
      // Already signed out server-side; clearing locally is enough.
    }
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({
      user,
      role: user?.role ?? null,
      isStaff: user?.role === 'teacher' || user?.role === 'administrator',
      loading,
      login,
      register,
      logout,
    }),
    [user, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
