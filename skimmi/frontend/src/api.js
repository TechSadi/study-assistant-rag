// Small wrappers around the FastAPI endpoints. All requests go to /api,
// which the Vite dev server proxies to the backend. Every request carries the
// Supabase session's access token; the backend verifies it and uses the user id
// inside it, so the frontend never sends a user id of its own.

import { supabase } from './supabase'

const API = '/api'

// The current access token. supabase-js refreshes it before it expires.
async function accessToken() {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

// A 401 means the session is gone (expired, revoked or signed out elsewhere).
// Try one refresh; if that fails, sign out so the sign-in screen appears.
async function refreshedToken() {
  const { data, error } = await supabase.auth.refreshSession()
  if (error || !data.session) {
    sessionStorage.setItem('skimmi-signed-out-reason', 'Your session has expired. Sign in again.')
    await supabase.auth.signOut({ scope: 'local' })
    return null
  }
  return data.session.access_token
}

function withAuth(options = {}, token) {
  return { ...options, headers: { ...options.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) } }
}

async function readError(response) {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return body.detail[0]?.msg ?? 'The request was invalid.'
  } catch {
    // Response wasn't JSON (for example, the proxy couldn't reach the backend).
  }
  if (response.status === 429) return "You're doing that too often. Wait a moment and try again."
  if (response.status >= 500) return "Skimmi's server isn't responding. Try again in a moment."
  return `Request failed (${response.status}).`
}

const OFFLINE = "Can't reach Skimmi's server. Check your connection and try again."

async function send(path, options) {
  try {
    return await fetch(`${API}${path}`, options)
  } catch {
    throw new Error(OFFLINE)
  }
}

// Returns the raw response after the auth retry (for downloads).
async function authorizedFetch(path, options) {
  let response = await send(path, withAuth(options, await accessToken()))
  if (response.status === 401) {
    const token = await refreshedToken()
    if (!token) throw new Error('Your session has expired. Sign in again.')
    response = await send(path, withAuth(options, token))
  }
  if (!response.ok) throw new Error(await readError(response))
  return response
}

async function request(path, options) {
  const response = await authorizedFetch(path, options)
  return response.status === 204 ? null : response.json()
}

export const getAccount = () => request('/me')

export const reindexDocument = (id) => request(`/documents/${id}/reindex`, { method: 'POST' })

// Downloads the original file (the link needs the auth header, so it's fetched first).
export async function downloadDocument(doc) {
  const response = await authorizedFetch(`/documents/${doc.id}/file`)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = doc.filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const listDocuments = () => request('/documents')

export const deleteDocument = (id) => request(`/documents/${id}`, { method: 'DELETE' })

// `changes` is { subject, course }. An empty subject removes both.
export const updateDocument = (id, changes) =>
  request(`/documents/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  })

export const getProgress = () => request('/progress')

// `answers` is the chosen option index for each question (null for a
// short-answer question). Returns the saved attempt with per-question `results`.
export const submitQuizAttempt = (messageId, answers) =>
  request(`/messages/${messageId}/quiz-attempts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  })

export const listConversations = () => request('/conversations')

export const getConversation = (id) => request(`/conversations/${id}`)

export const deleteConversation = (id) => request(`/conversations/${id}`, { method: 'DELETE' })

// `study` is { question, mode, doc_id, subject, topic, page_from, page_to }. The question
// may be empty outside ask/explain. Without a conversationId the backend starts
// a new conversation. Returns { conversation, question, message }.
export const askQuestion = (study, conversationId) =>
  request('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question: study.question,
      mode: study.mode ?? 'ask',
      doc_id: study.doc_id ?? null,
      subject: study.subject ?? null,
      topic: study.topic ?? null,
      page_from: study.page_from ?? null,
      page_to: study.page_to ?? null,
      conversation_id: conversationId ?? null,
    }),
  })

// fetch() can't report upload progress, so uploads use XMLHttpRequest.
// `subject` (optional) files the new document under that subject.
export async function uploadDocument(file, onProgress, subject) {
  try {
    return await sendUpload(file, onProgress, subject, await accessToken())
  } catch (e) {
    if (e.status !== 401) throw e
    const token = await refreshedToken()
    if (!token) throw new Error('Your session has expired. Sign in again.', { cause: e })
    return sendUpload(file, onProgress, subject, token)
  }
}

function sendUpload(file, onProgress, subject, token) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API}/documents`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total)
    }
    xhr.upload.onload = () => onProgress(1)

    xhr.onload = () => {
      let body = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // handled below
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body)
      const detail = typeof body?.detail === 'string' ? body.detail : null
      const error = new Error(detail ?? `Couldn't upload ${file.name} (${xhr.status}).`)
      error.status = xhr.status
      reject(error)
    }
    xhr.onerror = () => reject(new Error(OFFLINE))

    const form = new FormData()
    form.append('file', file)
    if (subject) form.append('subject', subject)
    xhr.send(form)
  })
}
