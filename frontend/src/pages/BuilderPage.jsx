// Hosts the wizard for both "new agent" and "edit agent".

import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import AgentBuilderWizard, { EMPTY_AGENT } from '../components/builder/AgentBuilderWizard.jsx'

// The API returns generated ids on child rows; the wizard submits plain values.
function toDraft(agent) {
  return {
    name: agent.name,
    description: agent.description ?? '',
    role_instructions: agent.role_instructions ?? '',
    audience_roles: agent.audience_roles ?? [],
    status: agent.status,
    model_settings: {
      model: agent.model_settings?.model ?? '',
      fallback_model: agent.model_settings?.fallback_model ?? '',
    },
    knowledge_sources: agent.knowledge_sources.map(({ source_type, label, location, notes }) => ({
      source_type,
      label,
      location,
      notes,
    })),
    tools: agent.tools.map(({ tool_key, enabled, config }) => ({ tool_key, enabled, config })),
    guardrails: agent.guardrails.map(({ guardrail_type, value }) => ({ guardrail_type, value })),
  }
}

export default function BuilderPage() {
  const { agentId } = useParams()
  const navigate = useNavigate()

  const [initialValue, setInitialValue] = useState(agentId ? null : EMPTY_AGENT)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  useEffect(() => {
    if (!agentId) return
    api(`/api/agents/${agentId}`)
      .then((agent) => setInitialValue(toDraft(agent)))
      .catch((err) => setSaveError(err.message))
  }, [agentId])

  async function handleSave(draft) {
    setSaving(true)
    setSaveError(null)
    try {
      await api(agentId ? `/api/agents/${agentId}` : '/api/agents', {
        method: agentId ? 'PUT' : 'POST',
        body: draft,
      })
      navigate('/agents')
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (agentId && !initialValue) {
    return saveError ? <p className="error-banner">{saveError}</p> : <div className="page-centre">Loading…</div>
  }

  return (
    <div className="content-column">
      <header className="content-header">
        <div>
          <h1>{agentId ? 'Edit agent' : 'New agent'}</h1>
          <p>Seven short steps. No code required.</p>
        </div>
      </header>

      <AgentBuilderWizard
        initialValue={initialValue}
        onSave={handleSave}
        saving={saving}
        saveError={saveError}
      />
    </div>
  )
}
