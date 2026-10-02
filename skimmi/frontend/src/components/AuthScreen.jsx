import { useState } from 'react'
import { AlertCircle, ArrowLeft, CheckCircle2, Eye, EyeOff, Loader2, MailCheck } from 'lucide-react'
import { Wordmark } from './Logo'
import { MIN_PASSWORD, authErrorMessage, passwordProblem, supabase } from '../supabase'

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const field =
  'mt-1.5 h-11 w-full rounded-lg border border-rule-strong bg-sheet px-3 text-[0.95rem] text-ink outline-none placeholder:text-ink-faint focus-visible:border-navy focus-visible:outline-none aria-[invalid=true]:border-danger disabled:bg-paper'

export function PasswordField({ label, value, onChange, autoComplete, invalid, describedBy, disabled }) {
  const [visible, setVisible] = useState(false)
  return (
    <label className="block text-sm font-medium text-ink">
      {label}
      <span className="relative block">
        <input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          disabled={disabled}
          required
          className={`${field} pr-11`}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="absolute top-1.5 right-1 grid size-9 place-items-center rounded-md text-ink-faint hover:text-ink"
          aria-label={visible ? 'Hide password' : 'Show password'}
        >
          {visible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
        </button>
      </span>
    </label>
  )
}

function Notice({ tone, children }) {
  const error = tone === 'error'
  const Icon = error ? AlertCircle : CheckCircle2
  return (
    <p
      role={error ? 'alert' : 'status'}
      className={`flex gap-2 rounded-lg px-3 py-2.5 text-sm ${error ? 'bg-danger-soft text-danger' : 'bg-ok-soft text-ok'}`}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </p>
  )
}

function SubmitButton({ busy, children }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-navy font-semibold text-white transition-colors hover:bg-navy-deep disabled:opacity-70"
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  )
}

const TITLES = {
  signin: ['Welcome back', 'Sign in to your study notes.'],
  signup: ['Create your account', 'Your documents and chats stay private to you.'],
  forgot: ['Reset your password', 'We’ll email you a link to choose a new one.'],
  reset: ['Choose a new password', 'You’ll stay signed in afterwards.'],
}

// Sign in, sign up, request a reset link, or (after following one) set a new password.
export default function AuthScreen({ initialView = 'signin', notice, onPasswordReset }) {
  const [view, setView] = useState(initialView)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sentTo, setSentTo] = useState('') // "check your email" after sign-up or a reset request
  const [info, setInfo] = useState(notice ?? '')

  function go(next) {
    setView(next)
    setError('')
    setInfo('')
    setPassword('')
    setConfirm('')
    setSentTo('')
  }

  function validate() {
    if (view !== 'reset' && !EMAIL.test(email.trim())) return 'Enter a valid email address.'
    if (view === 'signin' && !password) return 'Enter your password.'
    if (view === 'signup' || view === 'reset') {
      const problem = passwordProblem(password)
      if (problem) return problem
      if (password !== confirm) return 'The passwords don’t match.'
    }
    return ''
  }

  async function submit(e) {
    e.preventDefault()
    const problem = validate()
    setError(problem)
    setInfo('')
    if (problem) return
    setBusy(true)
    const address = email.trim()
    const redirectTo = `${window.location.origin}${window.location.pathname}`
    try {
      let result
      if (view === 'signin') {
        result = await supabase.auth.signInWithPassword({ email: address, password })
      } else if (view === 'signup') {
        result = await supabase.auth.signUp({ email: address, password, options: { emailRedirectTo: redirectTo } })
        // With email confirmation on (recommended), there's no session until the link is followed.
        if (!result.error && !result.data.session) setSentTo(address)
      } else if (view === 'forgot') {
        result = await supabase.auth.resetPasswordForEmail(address, { redirectTo })
        if (!result.error) setSentTo(address)
      } else {
        result = await supabase.auth.updateUser({ password })
        if (!result.error) onPasswordReset?.()
      }
      if (result.error) setError(authErrorMessage(result.error))
    } catch (err) {
      setError(authErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const [title, subtitle] = TITLES[view]
  const errorId = 'auth-error'

  return (
    <main className="flex min-h-dvh items-start justify-center overflow-y-auto bg-paper px-4 py-10 sm:items-center">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Wordmark />
        </div>
        <div className="rounded-2xl border border-rule bg-sheet p-6 shadow-[0_12px_32px_-20px_rgb(22_35_63/0.35)] sm:p-7">
          {sentTo ? (
            <div className="text-center">
              <span className="mx-auto grid size-12 place-items-center rounded-full bg-marker-soft text-navy">
                <MailCheck className="size-6" aria-hidden="true" />
              </span>
              <h1 className="mt-4 font-serif text-2xl font-medium text-ink">Check your email</h1>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                {view === 'signup'
                  ? <>We sent a confirmation link to <strong className="text-ink">{sentTo}</strong>. Follow it to finish creating your account.</>
                  : <>If an account exists for <strong className="text-ink">{sentTo}</strong>, we sent a link to reset its password.</>}
              </p>
              <button onClick={() => go('signin')} className="mt-6 text-sm font-semibold text-navy hover:underline">
                Back to sign in
              </button>
            </div>
          ) : (
            <>
              {view === 'forgot' && (
                <button
                  onClick={() => go('signin')}
                  className="-mt-1 mb-3 inline-flex items-center gap-1 text-sm font-medium text-ink-soft hover:text-ink"
                >
                  <ArrowLeft className="size-4" aria-hidden="true" /> Back
                </button>
              )}
              <h1 className="font-serif text-2xl font-medium text-ink">{title}</h1>
              <p className="mt-1 text-sm text-ink-soft">{subtitle}</p>

              <form onSubmit={submit} noValidate className="mt-6 space-y-4">
                {info && <Notice tone="ok">{info}</Notice>}
                {error && (
                  <div id={errorId}>
                    <Notice tone="error">{error}</Notice>
                  </div>
                )}
                {view !== 'reset' && (
                  <label className="block text-sm font-medium text-ink">
                    Email
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      autoComplete="email"
                      autoFocus
                      required
                      maxLength={320}
                      disabled={busy}
                      aria-describedby={error ? errorId : undefined}
                      className={field}
                    />
                  </label>
                )}
                {view !== 'forgot' && (
                  <PasswordField
                    label={view === 'reset' ? 'New password' : 'Password'}
                    value={password}
                    onChange={setPassword}
                    autoComplete={view === 'signin' ? 'current-password' : 'new-password'}
                    describedBy={view === 'signin' ? undefined : 'password-hint'}
                    disabled={busy}
                  />
                )}
                {(view === 'signup' || view === 'reset') && (
                  <>
                    <p id="password-hint" className="-mt-2 text-xs text-ink-soft">
                      At least {MIN_PASSWORD} characters, with letters and numbers.
                    </p>
                    <PasswordField
                      label="Confirm password"
                      value={confirm}
                      onChange={setConfirm}
                      autoComplete="new-password"
                      disabled={busy}
                    />
                  </>
                )}
                {view === 'signin' && (
                  <div className="-mt-1 text-right">
                    <button type="button" onClick={() => go('forgot')} className="text-sm font-medium text-navy hover:underline">
                      Forgot password?
                    </button>
                  </div>
                )}
                <SubmitButton busy={busy}>
                  {{ signin: 'Sign in', signup: 'Create account', forgot: 'Send reset link', reset: 'Save new password' }[view]}
                </SubmitButton>
              </form>

              {(view === 'signin' || view === 'signup') && (
                <p className="mt-6 text-center text-sm text-ink-soft">
                  {view === 'signin' ? 'New to Skimmi? ' : 'Already have an account? '}
                  <button onClick={() => go(view === 'signin' ? 'signup' : 'signin')} className="font-semibold text-navy hover:underline">
                    {view === 'signin' ? 'Create an account' : 'Sign in'}
                  </button>
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </main>
  )
}
