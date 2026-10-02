import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, FileUp, Loader2, Menu, SquarePen } from 'lucide-react'
import {
  askQuestion,
  deleteConversation,
  deleteDocument,
  downloadDocument,
  getConversation,
  getProgress,
  listConversations,
  listDocuments,
  reindexDocument,
  submitQuizAttempt,
  updateDocument,
  uploadDocument,
} from './api'
import Sidebar from './components/Sidebar'
import AccountPanel from './components/AccountPanel'
import StudyDashboard, { ProgressButton } from './components/Progress'
import { supabase } from './supabase'
import Composer from './components/Composer'
import { ModeMenu, ScopeMenu } from './components/StudyControls'
import { NewConversationState, NoDocumentsState } from './components/EmptyStates'
import { AssistantMessage, ErrorMessage, ThinkingMessage, UserMessage } from './components/Messages'
import { FlashcardsMessage, QuizMessage } from './components/Study'
import { modeById, parseStudyContent, studyRequest } from './modes'

const MAX_UPLOAD_MB = 25
// File types the backend can read (backend/app/extract.py FILE_TYPES).
const UPLOAD_TYPES = ['.pdf', '.docx', '.pptx', '.epub', '.txt', '.md', '.markdown']
const OLD_OFFICE_TYPES = { '.doc': '.docx', '.ppt': '.pptx' }
const extensionOf = (name) => (name.match(/\.[^.]+$/)?.[0] ?? '').toLowerCase()

let nextId = 0
const newId = () => ++nextId

// Saved messages from the API, shaped for rendering.
const fromServer = (m) => ({
  key: `m-${m.id}`,
  id: m.id,
  role: m.role,
  content: m.content,
  sources: m.sources,
  mode: m.mode,
  scope: m.scope,
  lastAttempt: m.last_attempt ?? null,
})

// docId or subject (never both) limits the material; a page range needs a document.
const NO_SCOPE = { docId: null, subject: null, topic: '', pageFrom: '', pageTo: '' }

// What a study mode sent without text asks for (the backend saves the same wording).
const DEFAULT_REQUESTS = {
  summarize: 'Summarize {target}',
  quiz: 'Quiz me on {target}',
  flashcards: 'Make flashcards for {target}',
}

function AssistantReply({ message: m, quizActions }) {
  const questions = m.mode === 'quiz' && parseStudyContent(m.content, 'questions')
  if (questions)
    return (
      <QuizMessage
        questions={questions}
        sources={m.sources}
        messageId={m.id}
        lastAttempt={m.lastAttempt}
        request={studyRequest(m.content)}
        {...quizActions}
      />
    )
  const cards = m.mode === 'flashcards' && parseStudyContent(m.content, 'cards')
  if (cards) return <FlashcardsMessage cards={cards} sources={m.sources} />
  return <AssistantMessage content={m.content} sources={m.sources} />
}

// The open chat's id lives in the URL (#chat=<id>) so a refresh reopens it.
function readChatFromUrl() {
  return window.location.hash.match(/^#chat=([\w-]+)$/)?.[1] ?? null
}
function writeChatToUrl(id) {
  const url = id ? `#chat=${id}` : window.location.pathname + window.location.search
  window.history.replaceState(null, '', url)
}

// `user` is the signed-in Supabase user (AuthGate renders App only with one).
export default function App({ user }) {
  const [documents, setDocuments] = useState([])
  const [docsStatus, setDocsStatus] = useState('loading') // 'loading' | 'ready' | 'error'
  const [docsError, setDocsError] = useState('')
  const [uploads, setUploads] = useState([])

  const [conversations, setConversations] = useState([])
  const [convStatus, setConvStatus] = useState('loading') // 'loading' | 'ready' | 'error'
  const [activeId, setActiveId] = useState(null) // null = a new, unsaved chat
  const [messages, setMessages] = useState([])
  const [asking, setAsking] = useState(false)
  const [openingChat, setOpeningChat] = useState(false)

  const [mode, setMode] = useState('ask')
  const [scope, setScope] = useState(NO_SCOPE) // the document or subject, pages and topic to study
  const [subjectFilter, setSubjectFilter] = useState(null) // the sidebar's list: null = all, '' = no subject

  const [progress, setProgress] = useState(null)
  const [progressStatus, setProgressStatus] = useState('loading') // 'loading' | 'ready' | 'error'

  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [toast, setToast] = useState(null)
  const [accountOpen, setAccountOpen] = useState(false)
  const [dashboardOpen, setDashboardOpen] = useState(false)
  const closeDashboard = useCallback(() => setDashboardOpen(false), [])

  const fileInput = useRef(null)
  const scrollArea = useRef(null)
  // Bumped whenever the visible chat changes, so a late response for a chat
  // the user has left doesn't land in the one they're looking at.
  const view = useRef(0)

  // --- Documents ------------------------------------------------------------

  const loadDocuments = useCallback(async () => {
    setDocsStatus('loading')
    try {
      setDocuments(await listDocuments())
      setDocsStatus('ready')
    } catch (e) {
      setDocsError(e.message)
      setDocsStatus('error')
    }
  }, [])

  useEffect(() => {
    loadDocuments()
  }, [loadDocuments])

  // While a document is being indexed (e.g. rebuilt after a server restart),
  // check back every few seconds until it's ready or has failed.
  const indexing = documents.some((d) => d.status === 'processing')
  useEffect(() => {
    if (!indexing) return
    const timer = setInterval(() => listDocuments().then(setDocuments, () => {}), 4000)
    return () => clearInterval(timer)
  }, [indexing])

  // Refreshed after anything that changes it: a finished quiz, a new answer, a deleted chat.
  const loadProgress = useCallback(async () => {
    try {
      setProgress(await getProgress())
      setProgressStatus('ready')
    } catch {
      setProgressStatus('error')
    }
  }, [])

  useEffect(() => {
    loadProgress()
  }, [loadProgress])

  function openDashboard() {
    setDashboardOpen(true)
    if (progressStatus === 'error') loadProgress()
  }

  // A subject disappears when its last document is removed or moved.
  function forgetMissingSubjects(docs) {
    const exists = (subject) => docs.some((d) => d.subject.toLowerCase() === subject.toLowerCase())
    setScope((s) => (s.subject && !exists(s.subject) ? { ...s, subject: null } : s))
    setSubjectFilter((f) => (f && !exists(f) ? null : f === '' && !docs.some((d) => !d.subject) ? null : f))
  }

  function showToast(text, tone = 'ok') {
    setToast({ id: newId(), text, tone })
  }

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  const updateUpload = (key, changes) =>
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...changes } : u)))

  async function handleFiles(fileList) {
    const files = Array.from(fileList ?? [])
    if (!files.length) return
    setSidebarOpen(true)
    setUploads((l) => l.filter((u) => u.status !== 'error')) // clear old failures
    const subject = subjectFilter || undefined // uploads go into the subject being viewed

    // Upload one at a time; each file is indexed on the server before the next starts.
    for (const file of files) {
      const key = newId()
      const extension = extensionOf(file.name)

      if (!UPLOAD_TYPES.includes(extension)) {
        const error = OLD_OFFICE_TYPES[extension]
          ? `${file.name} is an older Office file. Save it as ${OLD_OFFICE_TYPES[extension]} and upload that.`
          : `${file.name} can't be read. Upload PDF, Word (.docx), PowerPoint (.pptx), EPUB, text or Markdown files.`
        setUploads((l) => [...l, { key, name: file.name, status: 'error', error }])
        continue
      }
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        setUploads((l) => [...l, { key, name: file.name, status: 'error', error: `${file.name} is larger than ${MAX_UPLOAD_MB} MB.` }])
        continue
      }

      setUploads((l) => [...l, { key, name: file.name, status: 'uploading', progress: 0 }])
      try {
        const doc = await uploadDocument(
          file,
          (p) => updateUpload(key, p >= 1 ? { progress: 1, status: 'indexing' } : { progress: p }),
          subject,
        )
        setDocuments((d) => [...d, doc])
        setUploads((l) => l.filter((u) => u.key !== key))
        showToast(doc.subject ? `Added ${doc.filename} to ${doc.subject}` : `Added ${doc.filename}`)
      } catch (e) {
        updateUpload(key, { status: 'error', error: e.message })
      }
    }
  }

  async function handleDelete(doc) {
    try {
      await deleteDocument(doc.id)
      const remaining = documents.filter((x) => x.id !== doc.id)
      setDocuments((d) => d.filter((x) => x.id !== doc.id))
      setScope((s) => (s.docId === doc.id ? { ...s, docId: null, pageFrom: '', pageTo: '' } : s))
      forgetMissingSubjects(remaining)
      showToast(`Removed ${doc.filename}`)
      return true
    } catch (e) {
      showToast(e.message, 'error')
      return false
    }
  }

  async function handleReindex(doc) {
    setDocuments((list) => list.map((d) => (d.id === doc.id ? { ...d, status: 'processing', error: '' } : d)))
    try {
      const updated = await reindexDocument(doc.id)
      setDocuments((list) => list.map((d) => (d.id === updated.id ? updated : d)))
      showToast(`${updated.filename} is ready`)
    } catch (e) {
      showToast(e.message, 'error')
      loadDocuments()
    }
  }

  async function handleDownload(doc) {
    try {
      await downloadDocument(doc)
    } catch (e) {
      showToast(e.message, 'error')
    }
  }

  async function signOut() {
    await supabase.auth.signOut() // AuthGate then shows the sign-in screen
  }

  // Throws on failure, so the subject form can show the error.
  async function handleUpdateDocument(doc, changes) {
    const updated = await updateDocument(doc.id, changes)
    const next = documents.map((d) => (d.id === updated.id ? updated : d))
    setDocuments((list) => list.map((d) => (d.id === updated.id ? updated : d)))
    forgetMissingSubjects(next)
    showToast(updated.subject ? `Filed ${updated.filename} under ${updated.subject}` : `Removed the subject from ${updated.filename}`)
  }

  function openFilePicker() {
    fileInput.current?.click()
  }

  // Accept documents dropped anywhere on the window.
  useEffect(() => {
    let depth = 0
    const hasFiles = (e) => e.dataTransfer?.types?.includes('Files')
    const onEnter = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth++
      setDragging(true)
    }
    const onOver = (e) => hasFiles(e) && e.preventDefault()
    const onLeave = (e) => {
      if (!hasFiles(e)) return
      depth = Math.max(0, depth - 1)
      if (depth === 0) setDragging(false)
    }
    const onDrop = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth = 0
      setDragging(false)
      handleFiles(e.dataTransfer.files)
    }
    window.addEventListener('dragenter', onEnter)
    window.addEventListener('dragover', onOver)
    window.addEventListener('dragleave', onLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onEnter)
      window.removeEventListener('dragover', onOver)
      window.removeEventListener('dragleave', onLeave)
      window.removeEventListener('drop', onDrop)
    }
  })

  // --- Conversations --------------------------------------------------------

  useEffect(() => {
    listConversations()
      .then((list) => {
        setConversations(list)
        setConvStatus('ready')
      })
      .catch(() => setConvStatus('error'))

    const fromUrl = readChatFromUrl()
    if (fromUrl) openConversation(fromUrl)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function openConversation(id) {
    const current = ++view.current
    setActiveId(id)
    setMessages([])
    setAsking(false)
    setOpeningChat(true)
    setSidebarOpen(false)
    writeChatToUrl(id)
    try {
      const conversation = await getConversation(id)
      if (current !== view.current) return
      setMessages(conversation.messages.map(fromServer))
    } catch (e) {
      if (current !== view.current) return
      showToast(e.message, 'error')
      newChat()
    } finally {
      if (current === view.current) setOpeningChat(false)
    }
  }

  function newChat() {
    view.current++
    setActiveId(null)
    setMessages([])
    setAsking(false)
    setOpeningChat(false)
    writeChatToUrl(null)
  }

  async function handleDeleteConversation(conversation) {
    try {
      await deleteConversation(conversation.id)
      setConversations((list) => list.filter((c) => c.id !== conversation.id))
      if (conversation.id === activeId) newChat()
      loadProgress()
      showToast('Deleted chat')
      return true
    } catch (e) {
      showToast(e.message, 'error')
      return false
    }
  }

  // --- Chat -----------------------------------------------------------------

  // A request for the backend from the typed text and a mode and scope (the
  // current ones unless given), plus how to show it until the saved version comes back.
  function buildRequest(text, requestMode = mode, requestScope = scope) {
    const doc = documents.find((d) => d.id === requestScope.docId)
    const subject = !doc && requestScope.subject ? requestScope.subject : null
    const topic = requestScope.topic.trim()
    const pageFrom = doc && requestScope.pageFrom ? Number(requestScope.pageFrom) : null
    const pageTo = doc && requestScope.pageTo ? Number(requestScope.pageTo) : null

    const label = []
    if (doc) label.push(doc.filename)
    else if (subject) label.push(subject)
    if (pageFrom || pageTo) {
      const first = pageFrom ?? 1
      const last = Math.min(pageTo ?? doc.pages, doc.pages)
      label.push(first === last ? `page ${first}` : `pages ${first}–${last}`)
    }
    if (topic) label.push(topic)

    return {
      question: text,
      mode: requestMode,
      doc_id: doc?.id ?? null,
      subject,
      topic: topic || null,
      page_from: pageFrom,
      page_to: pageTo,
      display: text || DEFAULT_REQUESTS[requestMode].replace('{target}', topic || doc?.filename || subject || 'my documents'),
      scope: label.join(' · '),
    }
  }

  // "New quiz" after a quiz: another one on the material the quiz covered
  // (older quizzes don't record it, so they use the current selection).
  function newQuiz(covered) {
    const quizScope = covered
      ? {
          docId: covered.doc_id,
          subject: covered.subject,
          topic: covered.topic ?? '',
          pageFrom: covered.page_from ? String(covered.page_from) : '',
          pageTo: covered.page_to ? String(covered.page_to) : '',
        }
      : scope
    ask(buildRequest('', 'quiz', quizScope))
  }

  async function saveQuizAttempt(messageId, answers) {
    const attempt = await submitQuizAttempt(messageId, answers)
    loadProgress()
    return attempt
  }

  // `retry` is a failed attempt's error message: its request is sent again.
  async function ask(request, retry) {
    const current = view.current
    const conversationId = activeId
    const userKey = retry?.userKey ?? `local-${newId()}`
    setMessages((m) =>
      retry
        ? m.filter((x) => x.key !== retry.key)
        : [...m, { key: userKey, role: 'user', content: request.display, mode: request.mode, scope: request.scope }],
    )
    setAsking(true)

    try {
      const { conversation, question, message } = await askQuestion(request, conversationId)
      // The exchange is saved on the server, so the chat list updates even if the user moved on.
      setConversations((list) => [conversation, ...list.filter((c) => c.id !== conversation.id)])
      if (current !== view.current) return
      if (!conversationId) {
        setActiveId(conversation.id)
        writeChatToUrl(conversation.id)
      }
      setMessages((m) => [...m.filter((x) => x.key !== userKey), fromServer(question), fromServer(message)])
      loadProgress()
    } catch (e) {
      if (current !== view.current) return
      setMessages((m) => [...m, { key: `local-${newId()}`, role: 'error', content: e.message, request, userKey }])
    } finally {
      if (current === view.current) setAsking(false)
    }
  }

  // While waiting, keep the bottom in view. When an answer arrives, scroll to
  // its first line so long answers are read from the top.
  useEffect(() => {
    const el = scrollArea.current
    if (!el) return
    const last = messages[messages.length - 1]
    if (!asking && last && last.role !== 'user') {
      el.querySelector(`[data-msg="${last.key}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    }
  }, [messages, asking])

  // Close the mobile drawer with Escape.
  useEffect(() => {
    if (!sidebarOpen) return
    const onKey = (e) => e.key === 'Escape' && setSidebarOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sidebarOpen])

  // --- Layout ---------------------------------------------------------------

  const hasDocs = documents.length > 0
  // Only indexed documents can be chosen as a scope.
  const readyDocs = documents.filter((d) => (d.status ?? 'ready') === 'ready')
  const inChat = activeId !== null || messages.length > 0 || asking
  const selectedDoc = documents.find((d) => d.id === scope.docId)
  const subjectDocs = scope.subject ? documents.filter((d) => d.subject === scope.subject) : []
  const pendingMode = messages.findLast((m) => m.role === 'user')?.mode

  let composerDisabledReason = ''
  if (docsStatus === 'loading') composerDisabledReason = 'Loading your documents…'
  else if (!hasDocs) composerDisabledReason = 'Upload a document to start asking questions'

  return (
    <div className="relative flex h-dvh overflow-hidden">
      <input
        ref={fileInput}
        type="file"
        accept={UPLOAD_TYPES.join(',')}
        multiple
        className="hidden"
        onChange={(e) => {
          handleFiles(e.target.files)
          e.target.value = ''
        }}
      />

      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        documents={documents}
        docsStatus={docsStatus}
        docsError={docsError}
        onReloadDocs={loadDocuments}
        uploads={uploads}
        onDismissUpload={(key) => setUploads((l) => l.filter((u) => u.key !== key))}
        onUploadClick={openFilePicker}
        onDelete={handleDelete}
        onUpdateDocument={handleUpdateDocument}
        onReindex={handleReindex}
        onDownload={handleDownload}
        user={user}
        onOpenAccount={() => setAccountOpen(true)}
        subjectFilter={subjectFilter}
        onSubjectFilterChange={setSubjectFilter}
        conversations={conversations}
        convStatus={convStatus}
        activeConversationId={activeId}
        onOpenConversation={openConversation}
        onDeleteConversation={handleDeleteConversation}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-2 border-b border-rule bg-paper/90 px-3 backdrop-blur sm:px-6">
          <button
            onClick={() => setSidebarOpen(true)}
            className="grid size-10 place-items-center rounded-lg text-ink hover:bg-sheet lg:hidden"
            aria-label="Open documents and chats"
            aria-controls="sidebar-panel"
            aria-expanded={sidebarOpen}
          >
            <Menu className="size-5" />
          </button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-ink-soft">
              {!hasDocs
                ? 'No documents yet'
                : selectedDoc
                  ? `Studying ${selectedDoc.filename}`
                  : scope.subject
                    ? `Studying ${scope.subject} (${subjectDocs.length === 1 ? '1 document' : `${subjectDocs.length} documents`})`
                    : `Searching ${documents.length === 1 ? '1 document' : `all ${documents.length} documents`}`}
            </p>
          </div>
          <ProgressButton onClick={openDashboard} />
          <button
            onClick={newChat}
            disabled={!inChat}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-rule-strong bg-sheet px-3 text-sm font-semibold text-ink transition-colors hover:border-navy disabled:border-rule disabled:bg-transparent disabled:text-ink-faint"
          >
            <SquarePen className="size-4" aria-hidden="true" />
            New chat
          </button>
        </header>

        <div ref={scrollArea} className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <div className="mx-auto w-full max-w-3xl px-4 sm:px-6">
            {!inChat && docsStatus === 'ready' && !hasDocs && (
              <NoDocumentsState onUploadClick={openFilePicker} />
            )}
            {!inChat && hasDocs && (
              <NewConversationState
                documents={documents}
                selectedDoc={selectedDoc}
                subject={scope.subject}
                subjectDocs={subjectDocs}
                mode={modeById(mode)}
                onPick={(q) => ask(buildRequest(q))}
              />
            )}

            {openingChat && (
              <p role="status" className="flex items-center gap-2 py-10 text-sm text-ink-soft">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Opening chat…
              </p>
            )}

            {inChat && !openingChat && (
              <div role="log" aria-live="polite" aria-label="Conversation" className="space-y-8 py-8">
                {messages.map((m) => (
                  <div key={m.key} data-msg={m.key} className="scroll-mt-6">
                    {m.role === 'user' && <UserMessage content={m.content} mode={m.mode} scope={m.scope} />}
                    {m.role === 'assistant' && (
                      <AssistantReply
                        message={m}
                        quizActions={{ onSaveAttempt: saveQuizAttempt, onNewQuiz: newQuiz, busy: asking || openingChat }}
                      />
                    )}
                    {m.role === 'error' && (
                      <ErrorMessage content={m.content} retrying={asking} onRetry={() => ask(m.request, m)} />
                    )}
                  </div>
                ))}
                {asking && <ThinkingMessage mode={pendingMode} />}
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 px-3 pt-2 pb-3 sm:px-6 sm:pb-5">
          <Composer
            onSend={(q) => ask(buildRequest(q))}
            busy={asking || openingChat}
            disabled={!hasDocs}
            disabledReason={composerDisabledReason}
            mode={modeById(mode)}
            tools={
              hasDocs && (
                <>
                  <ModeMenu mode={mode} onModeChange={setMode} />
                  <ScopeMenu scope={scope} onScopeChange={setScope} documents={readyDocs} />
                </>
              )
            }
          />
        </div>
      </main>

      {dashboardOpen && (
        <StudyDashboard
          progress={progress}
          status={progressStatus}
          onReload={loadProgress}
          onOpenConversation={(id) => {
            setDashboardOpen(false)
            openConversation(id)
          }}
          onClose={closeDashboard}
        />
      )}

      {accountOpen && <AccountPanel user={user} onClose={() => setAccountOpen(false)} onSignOut={signOut} />}

      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-navy/15 p-6 backdrop-blur-[2px]">
          <div className="flex flex-col items-center rounded-2xl border-2 border-dashed border-navy bg-sheet px-10 py-8 text-center shadow-xl">
            <FileUp className="size-8 text-navy" aria-hidden="true" />
            <p className="mt-3 font-semibold text-ink">Drop files to add them</p>
            <p className="mt-1 text-sm text-ink-soft">They'll appear in your documents</p>
          </div>
        </div>
      )}

      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-24 z-50 flex justify-center px-4">
        {toast && (
          <p
            key={toast.id}
            className="flex items-center gap-2 rounded-full bg-ink px-4 py-2 text-sm font-medium text-white shadow-lg"
          >
            {toast.tone === 'error' ? (
              <AlertCircle className="size-4 text-red-300" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="size-4 text-marker" aria-hidden="true" />
            )}
            {toast.text}
          </p>
        )}
      </div>
    </div>
  )
}
