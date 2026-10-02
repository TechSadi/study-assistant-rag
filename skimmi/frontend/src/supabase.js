import { createClient } from '@supabase/supabase-js'

// Supabase Auth handles sign-up, sign-in, sessions and password recovery.
// Only the public URL and anon (publishable) key belong here: they are safe in
// the browser because every table has Row Level Security and all data goes
// through the backend. The service role key must never be added to the frontend.
// The project URL; a pasted Data API URL (…/rest/v1) works too.
const url = import.meta.env.VITE_SUPABASE_URL?.trim().replace(/\/rest\/v1\/?$/, '').replace(/\/$/, '')
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const supabaseConfigured = Boolean(url && anonKey)

export const supabase = supabaseConfigured
  ? createClient(url, anonKey, {
      auth: {
        persistSession: true, // stay signed in across reloads
        autoRefreshToken: true,
        detectSessionInUrl: true, // email confirmation and password recovery links
      },
    })
  : null

// Supabase's messages are written for developers; show students something clearer.
export function authErrorMessage(error) {
  const message = error?.message ?? ''
  if (error?.status === 429 || /rate limit|too many/i.test(message))
    return 'Too many attempts. Wait a minute and try again.'
  if (/invalid login credentials/i.test(message)) return 'That email and password don’t match an account.'
  if (/email not confirmed/i.test(message)) return 'Confirm your email first. Check your inbox for the link.'
  if (/already registered|already exists/i.test(message)) return 'An account with this email already exists. Sign in instead.'
  if (/password should be|weak password/i.test(message)) return 'Choose a stronger password: at least 8 characters, mixing letters and numbers.'
  if (/same.*password/i.test(message)) return 'Choose a password you haven’t used for this account.'
  if (/expired|invalid.*(token|link)|otp/i.test(message)) return 'This link has expired. Request a new one.'
  if (/fetch|network/i.test(message)) return 'Can’t reach the sign-in service. Check your connection and try again.'
  return 'Something went wrong. Try again.'
}

export const MIN_PASSWORD = 8

// Returns an error message, or '' if the password is acceptable. Also set the
// same minimum in Supabase (Authentication -> Providers -> Email), which enforces it.
export function passwordProblem(password) {
  if (password.length < MIN_PASSWORD) return `Use at least ${MIN_PASSWORD} characters.`
  if (!/[a-z]/i.test(password) || !/\d/.test(password)) return 'Use a mix of letters and numbers.'
  return ''
}
