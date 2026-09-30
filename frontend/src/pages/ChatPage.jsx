// Figure 3.7 layout: agent/conversation rail on the left, transcript in the middle,
// composer pinned to the bottom.
//
// Replies currently come from the stubbed runtime on the server. Nothing on this screen
// changes when Phase 2 swaps in a real model.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../auth.jsx'

function MessageBubble({ message }) {
  const isUser = message.sender === 'user'
  return (
    <div className={`bubble-row ${isUser ? 'from-user' : 'from-agent'}`}>
      <div className="bubble">
        <div className="bubble-sender">{isUser ? 'You' : 'Agent'}</div>
        <div className="bubble-content">{message.content}</div>
        {message.meta?.stub && (
          <div className="bubble-note">
            Placeholder response — the model layer arrives in Phase 2.
          </div>
        )}
      </div>
    </div>
  )
}

export default function ChatPage() {
  const { isStaff } = useAuth()

  const [agents, setAgents] = useState([])
  const [sessions, setSessions] = useState([])
  const [activeSession, setActiveSession] = useState(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const transcriptRef = useRef(null)

  const refreshSessions = useCallback(async () => {
    setSessions(await api('/api/chat/sessions'))
  }, [])

  useEffect(() => {
    Promise.all([api('/api/agents'), api('/api/chat/sessions')])
      .then(([agentList, sessionList]) => {
        setAgents(agentList)
        setSessions(sessionList)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  // Keep the newest message in view as the conversation grows.
  useEffect(() => {
    const node = transcriptRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [activeSession])

  async function startConversation(agentId) {
    setError(null)
    try {
      const session = await api('/api/chat/sessions', { method: 'POST', body: { agent_id: agentId } })
      setActiveSession(session)
      await refreshSessions()
    } catch (err) {
      setError(err.message)
    }
  }

  async function openConversation(sessionId) {
    setError(null)
    try {
      setActiveSession(await api(`/api/chat/sessions/${sessionId}`))
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleSend(event) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || !activeSession || sending) return

    setSending(true)
    setError(null)
    setDraft('')
    try {
      const { user_message, agent_message } = await api(
        `/api/chat/sessions/${activeSession.id}/messages`,
        { method: 'POST', body: { content } },
      )
      setActiveSession((current) => ({
        ...current,
        messages: [...current.messages, user_message, agent_message],
      }))
    } catch (err) {
      setError(err.message)
      setDraft(content)
    } finally {
      setSending(false)
    }
  }

  if (loading) return <div className="page-centre">Loading…</div>

  return (
    <div className="chat-layout">
      <aside className="chat-rail">
        <section className="rail-section">
          <h2 className="rail-heading">Agents</h2>
          {agents.length === 0 ? (
            <p className="rail-empty">
              No agents are available to you yet.
              {isStaff && (
                <>
                  {' '}
                  <Link to="/agents/new">Build one</Link>.
                </>
              )}
            </p>
          ) : (
            <ul className="rail-list">
              {agents.map((agent) => (
                <li key={agent.id}>
                  <button type="button" className="rail-item" onClick={() => startConversation(agent.id)}>
                    <span className="rail-item-title">{agent.name}</span>
                    {agent.description && (
                      <span className="rail-item-sub">{agent.description}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rail-section">
          <h2 className="rail-heading">Conversations</h2>
          {sessions.length === 0 ? (
            <p className="rail-empty">Pick an agent above to start one.</p>
          ) : (
            <ul className="rail-list">
              {sessions.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    className={`rail-item ${activeSession?.id === session.id ? 'is-active' : ''}`}
                    onClick={() => openConversation(session.id)}
                  >
                    <span className="rail-item-title">{session.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>

      <section className="chat-main">
        {error && <p className="error-banner">{error}</p>}

        {activeSession ? (
          <>
            <header className="chat-main-header">
              <h1>{activeSession.title}</h1>
            </header>

            <div className="transcript" ref={transcriptRef}>
              {activeSession.messages.length === 0 ? (
                <p className="transcript-empty">Ask a question to get started.</p>
              ) : (
                activeSession.messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))
              )}
            </div>

            <form className="composer" onSubmit={handleSend}>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Type a message…"
                rows={2}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    handleSend(e)
                  }
                }}
              />
              <button type="submit" className="primary-button" disabled={sending || !draft.trim()}>
                {sending ? 'Sending…' : 'Send'}
              </button>
            </form>
          </>
        ) : (
          <div className="page-centre">
            <p>Choose an agent on the left to begin a conversation.</p>
          </div>
        )}
      </section>
    </div>
  )
}
