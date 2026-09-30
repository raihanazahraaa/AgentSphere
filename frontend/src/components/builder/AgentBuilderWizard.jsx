// FR3 — the no-code agent builder.
//
// Five steps, one per thing a staff member configures: name, role, knowledge sources,
// tools, guardrails. Every option list comes from /api/agents/catalog, so a teacher picks
// from checkboxes and dropdowns and never types an identifier, a prompt template, or any
// code. The whole configuration is submitted as one document on the review step.

import { useEffect, useState } from 'react'
import { api } from '../../api.js'

const STEPS = [
  { key: 'name', title: 'Name', blurb: 'What is this agent called?' },
  { key: 'role', title: 'Role', blurb: 'What should it do, and who is it for?' },
  { key: 'model', title: 'Model', blurb: 'Which local model should answer?' },
  { key: 'knowledge', title: 'Knowledge', blurb: 'What may it read to find answers?' },
  { key: 'tools', title: 'Tools', blurb: 'What may it look up in the school system?' },
  { key: 'guardrails', title: 'Guardrails', blurb: 'What must it never do?' },
  { key: 'review', title: 'Review', blurb: 'Check everything, then save.' },
]

export const EMPTY_AGENT = {
  name: '',
  description: '',
  role_instructions: '',
  audience_roles: [],
  status: 'draft',
  model_settings: { model: '', fallback_model: '' },
  knowledge_sources: [],
  tools: [],
  guardrails: [],
}

function StepIndicator({ stepIndex, onJump }) {
  return (
    <ol className="wizard-steps">
      {STEPS.map((step, index) => (
        <li key={step.key}>
          <button
            type="button"
            className={`wizard-step ${index === stepIndex ? 'is-current' : ''} ${
              index < stepIndex ? 'is-done' : ''
            }`}
            onClick={() => onJump(index)}
          >
            <span className="wizard-step-number">{index + 1}</span>
            <span className="wizard-step-title">{step.title}</span>
          </button>
        </li>
      ))}
    </ol>
  )
}

export default function AgentBuilderWizard({ initialValue, onSave, saving, saveError }) {
  const [catalog, setCatalog] = useState(null)
  const [stepIndex, setStepIndex] = useState(0)
  const [draft, setDraft] = useState(initialValue ?? EMPTY_AGENT)
  const [loadError, setLoadError] = useState(null)

  useEffect(() => {
    api('/api/agents/catalog')
      .then(setCatalog)
      .catch((err) => setLoadError(err.message))
  }, [])

  useEffect(() => {
    if (initialValue) setDraft(initialValue)
  }, [initialValue])

  function patch(changes) {
    setDraft((current) => ({ ...current, ...changes }))
  }

  if (loadError) return <p className="error-banner">{loadError}</p>
  if (!catalog) return <div className="page-centre">Loading the builder…</div>

  const step = STEPS[stepIndex]
  const nameIsValid = draft.name.trim().length > 0

  return (
    <div className="wizard">
      <StepIndicator stepIndex={stepIndex} onJump={setStepIndex} />

      <div className="card wizard-panel">
        <header className="wizard-panel-header">
          <h2>{step.title}</h2>
          <p>{step.blurb}</p>
        </header>

        {step.key === 'name' && <NameStep draft={draft} patch={patch} />}
        {step.key === 'role' && <RoleStep draft={draft} patch={patch} catalog={catalog} />}
        {step.key === 'model' && <ModelStep draft={draft} patch={patch} catalog={catalog} />}
        {step.key === 'knowledge' && <KnowledgeStep draft={draft} patch={patch} catalog={catalog} />}
        {step.key === 'tools' && <ToolsStep draft={draft} patch={patch} catalog={catalog} />}
        {step.key === 'guardrails' && <GuardrailsStep draft={draft} patch={patch} catalog={catalog} />}
        {step.key === 'review' && <ReviewStep draft={draft} patch={patch} catalog={catalog} />}

        {saveError && <p className="error-text">{saveError}</p>}

        <footer className="wizard-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
            disabled={stepIndex === 0}
          >
            Back
          </button>

          {stepIndex < STEPS.length - 1 ? (
            <button
              type="button"
              className="primary-button"
              onClick={() => setStepIndex((i) => i + 1)}
              disabled={stepIndex === 0 && !nameIsValid}
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              className="primary-button"
              onClick={() => onSave(draft)}
              disabled={saving || !nameIsValid}
            >
              {saving ? 'Saving…' : 'Save agent'}
            </button>
          )}
        </footer>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------- step 1

function NameStep({ draft, patch }) {
  return (
    <>
      <label className="field">
        <span>Agent name</span>
        <input
          value={draft.name}
          onChange={(e) => patch({ name: e.target.value })}
          placeholder="Parent Helpdesk"
        />
      </label>
      <label className="field">
        <span>Short description</span>
        <input
          value={draft.description ?? ''}
          onChange={(e) => patch({ description: e.target.value })}
          placeholder="Answers everyday questions from parents about their child."
        />
      </label>
    </>
  )
}

// ---------------------------------------------------------------------------- step 2

function RoleStep({ draft, patch, catalog }) {
  function toggleAudience(roleKey) {
    const next = draft.audience_roles.includes(roleKey)
      ? draft.audience_roles.filter((r) => r !== roleKey)
      : [...draft.audience_roles, roleKey]
    patch({ audience_roles: next })
  }

  return (
    <>
      <label className="field">
        <span>What should this agent do?</span>
        <textarea
          rows={6}
          value={draft.role_instructions}
          onChange={(e) => patch({ role_instructions: e.target.value })}
          placeholder="Write it as you would explain the job to a new member of staff."
        />
        <small className="field-help">
          Plain English. You do not need to write a prompt — AgentSphere turns this into one.
        </small>
      </label>

      <fieldset className="field">
        <legend>Who may talk to it?</legend>
        <div className="checkbox-grid">
          {catalog.audience_roles.map((role) => (
            <label key={role.key} className="checkbox-row">
              <input
                type="checkbox"
                checked={draft.audience_roles.includes(role.key)}
                onChange={() => toggleAudience(role.key)}
              />
              <span>{role.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
    </>
  )
}

// ------------------------------------------------------------------- step 3: model

function ModelStep({ draft, patch, catalog }) {
  const models = catalog.models ?? { available: [], ollama_reachable: false }
  const settings = draft.model_settings ?? { model: '', fallback_model: '' }

  function patchModel(changes) {
    patch({ model_settings: { ...settings, ...changes } })
  }

  if (!models.ollama_reachable) {
    return (
      <p className="error-banner">
        No local models are available — the model service is not running. The agent will
        still save, and will use the site default once the service is back.
      </p>
    )
  }

  return (
    <>
      <label className="field">
        <span>Model to use</span>
        <select
          value={settings.model}
          onChange={(e) => patchModel({ model: e.target.value })}
        >
          <option value="">Site default ({models.default})</option>
          {models.available.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <small className="field-help">
          Runs on the school's own hardware — nothing is sent to an outside service.
        </small>
      </label>

      <label className="field">
        <span>If that model fails, use</span>
        <select
          value={settings.fallback_model}
          onChange={(e) => patchModel({ fallback_model: e.target.value })}
        >
          <option value="">Site default ({models.fallback})</option>
          {models.available.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <small className="field-help">
          Used automatically if the first model is busy, errors, or takes too long.
        </small>
      </label>
    </>
  )
}

// ---------------------------------------------------------------------------- step 4

function KnowledgeStep({ draft, patch, catalog }) {
  const [type, setType] = useState(catalog.knowledge_source_types[0].key)
  const [label, setLabel] = useState('')
  const [location, setLocation] = useState('')

  const definition = catalog.knowledge_source_types.find((t) => t.key === type)

  function addSource() {
    if (!label.trim() || !location.trim()) return
    patch({
      knowledge_sources: [
        ...draft.knowledge_sources,
        { source_type: type, label: label.trim(), location: location.trim(), notes: null },
      ],
    })
    setLabel('')
    setLocation('')
  }

  function removeSource(index) {
    patch({ knowledge_sources: draft.knowledge_sources.filter((_, i) => i !== index) })
  }

  return (
    <>
      <div className="inline-form">
        <label className="field">
          <span>Kind of source</span>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {catalog.knowledge_source_types.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
          <small className="field-help">{definition?.description}</small>
        </label>

        <label className="field">
          <span>Name it</span>
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Parent handbook" />
        </label>

        <label className="field">
          <span>{definition?.location_label ?? 'Location'}</span>
          {definition?.options ? (
            <select value={location} onChange={(e) => setLocation(e.target.value)}>
              <option value="">Choose…</option>
              {definition.options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder={definition?.location_placeholder}
            />
          )}
        </label>

        <button type="button" className="secondary-button" onClick={addSource}>
          Add source
        </button>
      </div>

      <ItemList
        items={draft.knowledge_sources}
        emptyText="No sources yet. The agent will answer from its instructions alone."
        onRemove={removeSource}
        render={(source) => (
          <>
            <strong>{source.label}</strong>
            <span className="item-sub">
              {catalog.knowledge_source_types.find((t) => t.key === source.source_type)?.label} ·{' '}
              {source.location}
            </span>
          </>
        )}
      />
      <p className="field-help">
        Sources are recorded now and indexed for retrieval in Phase 3.
      </p>
    </>
  )
}

// ---------------------------------------------------------------------------- step 4

function ToolsStep({ draft, patch, catalog }) {
  const enabledKeys = draft.tools.filter((t) => t.enabled).map((t) => t.tool_key)

  function toggleTool(toolKey) {
    const next = enabledKeys.includes(toolKey)
      ? draft.tools.filter((t) => t.tool_key !== toolKey)
      : [...draft.tools, { tool_key: toolKey, enabled: true, config: {} }]
    patch({ tools: next })
  }

  return (
    <>
      <ul className="option-list">
        {catalog.tools.map((tool) => (
          <li key={tool.key}>
            <label className={`option-row ${tool.available === false ? 'is-disabled' : ''}`}>
              <input
                type="checkbox"
                checked={enabledKeys.includes(tool.key)}
                onChange={() => toggleTool(tool.key)}
                disabled={tool.available === false}
              />
              <span>
                <strong>{tool.label}</strong>
                {tool.available === false && (
                  <span className="soon-pill">
                    coming in phase {tool.coming_in_phase ?? 3}
                  </span>
                )}
                <span className="item-sub">{tool.description}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      <p className="field-help">
        A tool only ever returns what the person asking is already allowed to see — a parent
        asking about attendance gets their own child's, never another family's.
      </p>
    </>
  )
}

// ---------------------------------------------------------------------------- step 5

function GuardrailsStep({ draft, patch, catalog }) {
  const [type, setType] = useState(catalog.guardrail_types[0].key)
  const [value, setValue] = useState('')

  const definition = catalog.guardrail_types.find((t) => t.key === type)

  function addGuardrail() {
    if (!value.trim()) return
    patch({ guardrails: [...draft.guardrails, { guardrail_type: type, value: value.trim() }] })
    setValue('')
  }

  function removeGuardrail(index) {
    patch({ guardrails: draft.guardrails.filter((_, i) => i !== index) })
  }

  return (
    <>
      <div className="inline-form">
        <label className="field">
          <span>Rule</span>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {catalog.guardrail_types.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
          <small className="field-help">{definition?.help}</small>
        </label>

        <label className="field">
          <span>{definition?.input_label ?? 'Value'}</span>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={definition?.placeholder}
          />
        </label>

        <button type="button" className="secondary-button" onClick={addGuardrail}>
          Add rule
        </button>
      </div>

      <ItemList
        items={draft.guardrails}
        emptyText="No rules yet."
        onRemove={removeGuardrail}
        render={(rail) => (
          <>
            <strong>
              {catalog.guardrail_types.find((t) => t.key === rail.guardrail_type)?.label}
            </strong>
            <span className="item-sub">{rail.value}</span>
          </>
        )}
      />
    </>
  )
}

// ---------------------------------------------------------------------------- step 6

function ReviewStep({ draft, patch, catalog }) {
  const audienceLabels = draft.audience_roles
    .map((key) => catalog.audience_roles.find((r) => r.key === key)?.label ?? key)
    .join(', ')

  return (
    <>
      <dl className="review-grid">
        <dt>Name</dt>
        <dd>{draft.name || <em>not set</em>}</dd>

        <dt>Description</dt>
        <dd>{draft.description || <em>none</em>}</dd>

        <dt>Role</dt>
        <dd>{draft.role_instructions || <em>none</em>}</dd>

        <dt>Audience</dt>
        <dd>{audienceLabels || <em>nobody yet</em>}</dd>

        <dt>Model</dt>
        <dd>
          {draft.model_settings?.model || <em>site default</em>}
          {draft.model_settings?.fallback_model
            ? ` (falls back to ${draft.model_settings.fallback_model})`
            : ''}
        </dd>

        <dt>Knowledge</dt>
        <dd>
          {draft.knowledge_sources.length
            ? draft.knowledge_sources.map((s) => s.label).join(', ')
            : <em>none</em>}
        </dd>

        <dt>Tools</dt>
        <dd>
          {draft.tools.length
            ? draft.tools
                .map((t) => catalog.tools.find((c) => c.key === t.tool_key)?.label ?? t.tool_key)
                .join(', ')
            : <em>none</em>}
        </dd>

        <dt>Guardrails</dt>
        <dd>{draft.guardrails.length ? `${draft.guardrails.length} rule(s)` : <em>none</em>}</dd>
      </dl>

      <fieldset className="field">
        <legend>Publish?</legend>
        <div className="checkbox-grid">
          <label className="checkbox-row">
            <input
              type="radio"
              name="status"
              checked={draft.status === 'draft'}
              onChange={() => patch({ status: 'draft' })}
            />
            <span>Keep as a draft — only staff can see it</span>
          </label>
          <label className="checkbox-row">
            <input
              type="radio"
              name="status"
              checked={draft.status === 'published'}
              onChange={() => patch({ status: 'published' })}
            />
            <span>Publish to the audience chosen above</span>
          </label>
        </div>
      </fieldset>
    </>
  )
}

// ------------------------------------------------------------------------------ shared

function ItemList({ items, emptyText, onRemove, render }) {
  if (items.length === 0) return <p className="rail-empty">{emptyText}</p>
  return (
    <ul className="item-list">
      {items.map((item, index) => (
        <li key={index}>
          <div className="item-body">{render(item)}</div>
          <button type="button" className="link-button" onClick={() => onRemove(index)}>
            Remove
          </button>
        </li>
      ))}
    </ul>
  )
}
