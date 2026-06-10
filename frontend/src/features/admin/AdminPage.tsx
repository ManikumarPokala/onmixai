// The owner/admin console. Two panels wired to the real admin API, each handling
// loading / empty / error / success explicitly (CLAUDE.md §10):
//   • Users — deactivating a user is destructive (it kills their sessions immediately), so it is
//     gated behind a consequence-confirm dialog.
//   • AI configuration — disabling PII redaction means personal data may appear in answers shown
//     to users, so turning it OFF is also gated behind a consequence-confirm. (It stays decoupled
//     from telemetry: traces/logs/audit never carry raw PII regardless of this toggle.)

import { useState } from 'react'
import { ConsequenceConfirm } from '../../components/ConsequenceConfirm'
import { useToast } from '../../lib/toast/useToast'
import { useAdminUsers, useDeactivateUser, useModelConfig, useSetModelConfig } from './api'

function UsersPanel() {
  const { data, isLoading, isError } = useAdminUsers()
  const deactivate = useDeactivateUser()
  const toast = useToast()
  const [pending, setPending] = useState<{ id: string; name: string } | null>(null)

  if (isLoading) return <p>Loading users…</p>
  if (isError) return <p role="alert">Could not load users. Please try again.</p>
  const users = data?.users ?? []
  if (users.length === 0) return <p>No users yet.</p>

  return (
    <section aria-labelledby="users-heading">
      <h2 id="users-heading">Users</h2>
      <ul className="admin-users">
        {users.map((u) => (
          <li key={u.id}>
            <span>
              {u.full_name} · {u.role} · {u.is_active ? 'active' : 'inactive'}
            </span>
            {u.is_active && (
              <button
                type="button"
                className="btn btn--danger"
                onClick={() => setPending({ id: u.id, name: u.full_name })}
              >
                Deactivate
              </button>
            )}
          </li>
        ))}
      </ul>
      <ConsequenceConfirm
        open={pending !== null}
        title="Deactivate user?"
        message={`This signs ${pending?.name ?? 'the user'} out of every session immediately and blocks new sign-ins until reactivated.`}
        confirmLabel="Deactivate"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending)
            deactivate.mutate(pending.id, {
              onSuccess: () => toast.notify('User deactivated.', 'success'),
              onError: toast.notifyError,
            })
          setPending(null)
        }}
      />
    </section>
  )
}

function AiConfigPanel() {
  const { data, isLoading, isError } = useModelConfig()
  const save = useSetModelConfig()
  const toast = useToast()
  const [confirmingDisable, setConfirmingDisable] = useState(false)

  if (isLoading) return <p>Loading AI configuration…</p>
  if (isError || !data) return <p role="alert">Could not load AI configuration. Please try again.</p>

  function persist(piiEnabled: boolean) {
    if (!data) return
    save.mutate(
      {
        default_model: data.default_model,
        fallback_chain: data.fallback_chain,
        temperature_default: data.temperature_default,
        pii_redaction_enabled: piiEnabled,
      },
      {
        onSuccess: () => toast.notify('AI configuration saved.', 'success'),
        onError: toast.notifyError,
      },
    )
  }

  return (
    <section aria-labelledby="ai-heading">
      <h2 id="ai-heading">AI configuration</h2>
      <p>
        Default model: <strong>{data.default_model}</strong>
      </p>
      <label className="admin-toggle">
        <input
          type="checkbox"
          checked={data.pii_redaction_enabled}
          onChange={(e) => {
            // Enabling redaction is safe and applies immediately; DISABLING it is consequential.
            if (e.target.checked) persist(true)
            else setConfirmingDisable(true)
          }}
        />
        Redact PII in answers
      </label>
      <ConsequenceConfirm
        open={confirmingDisable}
        title="Disable PII redaction?"
        message="Personal data (emails, phone numbers, IDs) from your documents may then appear in answers shown to your users. Traces, logs, and the audit trail are unaffected — they never store raw content."
        confirmLabel="Disable redaction"
        onCancel={() => setConfirmingDisable(false)}
        onConfirm={() => {
          persist(false)
          setConfirmingDisable(false)
        }}
      />
    </section>
  )
}

export function AdminPage() {
  return (
    <div className="admin-console">
      <h1>Administration</h1>
      <UsersPanel />
      <AiConfigPanel />
    </div>
  )
}
