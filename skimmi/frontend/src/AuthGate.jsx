import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import App from './App'
import AuthScreen from './components/AuthScreen'
import { supabase, supabaseConfigured } from './supabase'

// Protects the whole app: it renders only for a signed-in user. The session is
// kept by supabase-js (in localStorage) so a reload stays signed in. The backend
// checks the token on every request regardless, so this is about UX, not security.
export default function AuthGate() {
  const [session, setSession] = useState(undefined) // undefined while loading
  const [recovering, setRecovering] = useState(false) // arrived from a password reset link
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (!supabaseConfigured) return
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data } = supabase.auth.onAuthStateChange((event, next) => {
      if (event === 'PASSWORD_RECOVERY') setRecovering(true)
      if (event === 'SIGNED_OUT') {
        setNotice(sessionStorage.getItem('skimmi-signed-out-reason') ?? '')
        sessionStorage.removeItem('skimmi-signed-out-reason')
        window.history.replaceState(null, '', window.location.pathname) // forget the open chat
      }
      setSession(next)
    })
    return () => data.subscription.unsubscribe()
  }, [])

  if (!supabaseConfigured) {
    return (
      <main className="grid min-h-dvh place-items-center bg-paper p-6">
        <div className="max-w-md rounded-2xl border border-rule bg-sheet p-6 text-sm leading-relaxed text-ink-soft">
          <h1 className="font-serif text-xl font-medium text-ink">Sign-in isn’t set up</h1>
          <p className="mt-2">
            Add <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_ANON_KEY</code> to{' '}
            <code>skimmi/frontend/.env</code> (see <code>.env.example</code>) and restart the dev server.
          </p>
        </div>
      </main>
    )
  }

  if (session === undefined) {
    return (
      <main className="grid min-h-dvh place-items-center bg-paper" role="status">
        <Loader2 className="size-6 animate-spin text-navy" aria-label="Loading" />
      </main>
    )
  }

  if (recovering) {
    return <AuthScreen initialView="reset" onPasswordReset={() => setRecovering(false)} />
  }

  if (!session) return <AuthScreen notice={notice} />

  // Keyed by user, so switching accounts starts from a clean state.
  return <App key={session.user.id} user={session.user} />
}
