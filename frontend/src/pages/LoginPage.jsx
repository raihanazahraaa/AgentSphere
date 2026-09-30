import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth.jsx'

const ROLES = [
  { value: 'parent', label: 'Parent' },
  { value: 'teacher', label: 'Teacher' },
  { value: 'administrator', label: 'Administrator' },
]

export default function LoginPage() {
  const { user, loading, login, register } = useAuth()
  const navigate = useNavigate()

  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState('teacher')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  if (loading) return <div className="page-centre auth-page">Loading…</div>
  if (user) return <Navigate to="/chat" replace />

  async function handleSubmit(event) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'login') {
        await login(email, password)
      } else {
        await register({ email, password, full_name: fullName, role })
      }
      navigate('/chat', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-centre auth-page">
      <form className="card auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">AgentSphere</h1>
        <p className="auth-subtitle">
          {mode === 'login' ? 'Sign in to continue.' : 'Create an account for your school.'}
        </p>

        {mode === 'register' && (
          <label className="field">
            <span>Full name</span>
            <input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
              autoComplete="name"
            />
          </label>
        )}

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={mode === 'register' ? 8 : undefined}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          />
        </label>

        {mode === 'register' && (
          <label className="field">
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="primary-button" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>

        <button
          type="button"
          className="link-button auth-switch"
          onClick={() => {
            setMode(mode === 'login' ? 'register' : 'login')
            setError(null)
          }}
        >
          {mode === 'login' ? 'Need an account? Register' : 'Already registered? Sign in'}
        </button>

        <p className="auth-hint">
          Demo accounts (password <code>password123</code>): admin@greenfield.edu,
          teacher@greenfield.edu, parent@greenfield.edu
        </p>
      </form>
    </div>
  )
}
