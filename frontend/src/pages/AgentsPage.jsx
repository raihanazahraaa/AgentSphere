// Staff-only list of configured agents (FR3). Parents never reach this route.

import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'

export default function AgentsPage() {
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    api('/api/agents')
      .then(setAgents)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleDelete(agent) {
    if (!window.confirm(`Delete "${agent.name}"? This cannot be undone.`)) return
    try {
      await api(`/api/agents/${agent.id}`, { method: 'DELETE' })
      setAgents((current) => current.filter((a) => a.id !== agent.id))
    } catch (err) {
      setError(err.message)
    }
  }

  if (loading) return <div className="page-centre">Loading…</div>

  return (
    <div className="content-column">
      <header className="content-header">
        <div>
          <h1>Agents</h1>
          <p>Configure an assistant without writing any code.</p>
        </div>
        <button type="button" className="primary-button" onClick={() => navigate('/agents/new')}>
          New agent
        </button>
      </header>

      {error && <p className="error-banner">{error}</p>}

      {agents.length === 0 ? (
        <p className="rail-empty">No agents yet. Start with “New agent”.</p>
      ) : (
        <ul className="agent-cards">
          {agents.map((agent) => (
            <li key={agent.id} className="card agent-card">
              <div className="agent-card-head">
                <h2>{agent.name}</h2>
                <span className={`status-pill status-${agent.status}`}>{agent.status}</span>
              </div>
              {agent.description && <p className="item-sub">{agent.description}</p>}
              <p className="item-sub">
                For: {agent.audience_roles.length ? agent.audience_roles.join(', ') : 'nobody yet'}
              </p>
              <div className="agent-card-actions">
                <Link to={`/agents/${agent.id}/edit`}>Edit</Link>
                <button type="button" className="link-button" onClick={() => handleDelete(agent)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
