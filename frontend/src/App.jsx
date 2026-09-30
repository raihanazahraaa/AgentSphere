import { Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell.jsx'
import { useAuth } from './auth.jsx'
import AgentsPage from './pages/AgentsPage.jsx'
import BuilderPage from './pages/BuilderPage.jsx'
import ChatPage from './pages/ChatPage.jsx'
import LoginPage from './pages/LoginPage.jsx'

// Routes are gated by role: only teachers and administrators reach the builder.
function Protected({ children, staffOnly = false }) {
  const { user, isStaff, loading } = useAuth()
  if (loading) return <div className="page-centre">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  if (staffOnly && !isStaff) return <Navigate to="/chat" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <Protected>
            <AppShell />
          </Protected>
        }
      >
        <Route path="/chat" element={<ChatPage />} />
        <Route
          path="/agents"
          element={
            <Protected staffOnly>
              <AgentsPage />
            </Protected>
          }
        />
        <Route
          path="/agents/new"
          element={
            <Protected staffOnly>
              <BuilderPage />
            </Protected>
          }
        />
        <Route
          path="/agents/:agentId/edit"
          element={
            <Protected staffOnly>
              <BuilderPage />
            </Protected>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  )
}
