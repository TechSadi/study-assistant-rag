import { useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2, LogOut, X } from 'lucide-react'
import { getAccount } from '../api'
import { authErrorMessage, passwordProblem, supabase } from '../supabase'
import { PasswordField } from './AuthScreen'

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }) : ''
}

// The signed-in user's details, a password change, and signing out.
export default function AccountPanel({ user, onClose, onSignOut }) {
  const [account, setAccount] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState(null) // { tone, text }
  const [signingOut, setSigningOut] = useState(false)
  const panel = useRef(null)

  useEffect(() => {
    getAccount().then(setAccount, (e) => setLoadError(e.message))
  }, [])

  useEffect(() => {
    panel.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function changePassword(e) {
    e.preventDefault()
    const problem = passwordProblem(password) || (password !== confirm ? 'The passwords don’t match.' : '')
    if (problem) return setResult({ tone: 'error', text: problem })
    setSaving(true)
    setResult(null)
    const { error } = await supabase.auth.updateUser({ password })
    setSaving(false)
    if (error) return setResult({ tone: 'error', text: authErrorMessage(error) })
    setPassword('')
    setConfirm('')
    setResult({ tone: 'ok', text: 'Your password was changed.' })
  }

  async function signOut() {
    setSigningOut(true)
    await onSignOut()
  }

  const stats = account && [
    [account.documents, account.documents === 1 ? 'document' : 'documents'],
    [account.conversations, account.conversations === 1 ? 'chat' : 'chats'],
    [account.quiz_attempts, account.quiz_attempts === 1 ? 'quiz taken' : 'quizzes taken'],
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/30 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <section
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="account-title"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90dvh] w-full overflow-y-auto rounded-t-2xl bg-sheet p-6 shadow-xl outline-none sm:max-w-md sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id="account-title" className="font-serif text-2xl font-medium text-ink">Your account</h2>
            <p className="mt-1 truncate text-sm text-ink-soft" title={user.email}>{user.email}</p>
          </div>
          <button onClick={onClose} className="-mt-1 -mr-2 grid size-10 place-items-center rounded-lg text-ink-soft hover:bg-paper" aria-label="Close">
            <X className="size-5" />
          </button>
        </div>

        <div className="mt-5 rounded-xl border border-rule bg-paper/60 p-4">
          {loadError && <p className="text-sm text-danger">{loadError}</p>}
          {!account && !loadError && (
            <p className="flex items-center gap-2 text-sm text-ink-soft" role="status">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading…
            </p>
          )}
          {account && (
            <>
              <p className="text-xs text-ink-soft">Member since {formatDate(account.created_at)}</p>
              <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                {stats.map(([value, label]) => (
                  <div key={label} className="rounded-lg bg-sheet px-2 py-2.5">
                    <dt className="sr-only">{label}</dt>
                    <dd className="text-lg font-semibold text-ink">{value}</dd>
                    <dd className="text-xs text-ink-soft" aria-hidden="true">{label}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </div>

        <form onSubmit={changePassword} noValidate className="mt-6 space-y-3">
          <h3 className="text-sm font-semibold text-ink">Change password</h3>
          {result && (
            <p
              role={result.tone === 'error' ? 'alert' : 'status'}
              className={`flex gap-2 rounded-lg px-3 py-2 text-sm ${result.tone === 'error' ? 'bg-danger-soft text-danger' : 'bg-ok-soft text-ok'}`}
            >
              {result.tone === 'error'
                ? <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                : <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
              {result.text}
            </p>
          )}
          <PasswordField label="New password" value={password} onChange={setPassword} autoComplete="new-password" disabled={saving} />
          <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" disabled={saving} />
          <button
            type="submit"
            disabled={saving || !password}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-rule-strong bg-sheet px-4 text-sm font-semibold text-ink hover:border-navy disabled:text-ink-faint"
          >
            {saving && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
            Update password
          </button>
        </form>

        <div className="mt-6 border-t border-rule pt-5">
          <button
            onClick={signOut}
            disabled={signingOut}
            className="inline-flex h-10 items-center gap-2 rounded-lg px-3 text-sm font-semibold text-danger hover:bg-danger-soft disabled:opacity-60"
          >
            {signingOut ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <LogOut className="size-4" aria-hidden="true" />}
            Sign out
          </button>
        </div>
      </section>
    </div>
  )
}
