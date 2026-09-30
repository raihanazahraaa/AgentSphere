// Top bar shared by every signed-in screen. The role badge is always visible, because
// which role you are signed in as changes what the agents will tell you.

import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth.jsx'

const ROLE_LABELS = {
  parent: 'Parent',
  teacher: 'Teacher',
  administrator: 'Administrator',
}

export default function AppShell() {
  const { user, role, isStaff, logout } = useAuth()
  const navigate = useNavigate()

  async function handleSignOut() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="shell">
      <header className="shell-header">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <span className="brand-name">AgentSphere</span>
        </div>

        <nav className="shell-nav">
          <NavLink to="/chat">Chat</NavLink>
          {isStaff && <NavLink to="/agents">Agents</NavLink>}
        </nav>

        <div className="shell-user">
          <span className="user-name">{user?.full_name}</span>
          <span className={`role-badge role-${role}`}>{ROLE_LABELS[role] ?? role}</span>
          <button type="button" className="link-button" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </header>

      <main className="shell-body">
        <Outlet />
      </main>
    </div>
  )
}
