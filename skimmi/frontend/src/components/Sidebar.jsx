import { useEffect, useId, useRef, useState } from 'react'
import { AlertCircle, Download, FileText, Loader2, MessagesSquare, RotateCw, Tag, Trash2, Upload, UserRound, X } from 'lucide-react'
import { Wordmark } from './Logo'
import ConversationList from './ConversationList'
import RowMenu from './RowMenu'
import { coursesOf, groupBySubject, subjectsOf } from '../subjects'

export default function Sidebar({
  open,
  onClose,
  documents,
  docsStatus,
  docsError,
  onReloadDocs,
  uploads,
  onDismissUpload,
  onUploadClick,
  onDelete,
  onUpdateDocument,
  onReindex,
  onDownload,
  user,
  onOpenAccount,
  subjectFilter,
  onSubjectFilterChange,
  conversations,
  convStatus,
  activeConversationId,
  onOpenConversation,
  onDeleteConversation,
}) {
  const totalPages = documents.reduce((sum, d) => sum + d.pages, 0)
  const subjects = subjectsOf(documents)
  const organized = subjects.length > 0
  // null shows every document, '' those without a subject, otherwise one subject's.
  const groups = groupBySubject(documents).filter((g) => subjectFilter == null || g.subject === subjectFilter)
  const [tab, setTab] = useState('chats')
  const tabsId = useId()

  // Show the Documents tab when a new upload starts, so its progress is visible.
  const uploadCount = useRef(uploads.length)
  useEffect(() => {
    if (uploads.length > uploadCount.current) setTab('documents')
    uploadCount.current = uploads.length
  }, [uploads.length])

  return (
    <>
      {/* Dimmed backdrop behind the drawer on small screens */}
      <div
        className={`fixed inset-0 z-30 bg-ink/30 transition-opacity lg:hidden ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        id="sidebar-panel"
        aria-label="Documents and chats"
        className={`fixed inset-y-0 left-0 z-40 flex w-[19rem] max-w-[85vw] flex-col border-r border-rule bg-sheet transition-transform duration-200 lg:static lg:z-auto lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between px-5">
          <Wordmark />
          <button
            onClick={onClose}
            className="-mr-2 grid size-10 place-items-center rounded-lg text-ink-soft hover:bg-paper lg:hidden"
            aria-label="Close sidebar"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="px-4 pb-4">
          <button
            onClick={onUploadClick}
            className="group flex w-full items-center gap-3 rounded-xl border border-dashed border-rule-strong bg-paper/60 px-4 py-3.5 text-left transition-colors hover:border-navy hover:bg-paper"
          >
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-navy text-white">
              <Upload className="size-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-ink">Upload document</span>
              <span className="block truncate text-xs text-ink-soft">
                {subjectFilter ? `Adds to ${subjectFilter} · or drop files` : 'or drop files anywhere'}
              </span>
            </span>
          </button>
        </div>

        <div className="shrink-0 px-4 pb-3">
          <div
            role="tablist"
            aria-label="Sidebar sections"
            className="grid grid-cols-2 gap-1 rounded-xl border border-rule bg-paper p-1"
            onKeyDown={(e) => {
              if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
              e.preventDefault()
              const next = tab === 'chats' ? 'documents' : 'chats'
              setTab(next)
              document.getElementById(`${tabsId}-${next}-tab`)?.focus()
            }}
          >
            <TabButton id={tabsId} tab="chats" active={tab === 'chats'} onSelect={setTab} icon={MessagesSquare} count={conversations.length}>
              Chats
            </TabButton>
            <TabButton id={tabsId} tab="documents" active={tab === 'documents'} onSelect={setTab} icon={FileText} count={documents.length}>
              Documents
            </TabButton>
          </div>
        </div>

        {/* Both panels stay mounted so each keeps its own scroll position and row state. */}
        <div
          role="tabpanel"
          id={`${tabsId}-chats-panel`}
          aria-labelledby={`${tabsId}-chats-tab`}
          hidden={tab !== 'chats'}
          className="min-h-0 flex-1 overflow-y-auto pb-4"
        >
          <ConversationList
            conversations={conversations}
            status={convStatus}
            activeId={activeConversationId}
            onOpen={onOpenConversation}
            onDelete={onDeleteConversation}
          />
        </div>

        <div
          role="tabpanel"
          id={`${tabsId}-documents-panel`}
          aria-labelledby={`${tabsId}-documents-tab`}
          hidden={tab !== 'documents'}
          className="min-h-0 flex-1 overflow-y-auto pb-4"
        >
        {documents.length > 0 && (
          <p className="px-5 pb-2 text-xs text-ink-soft">
            {documents.length} {documents.length === 1 ? 'file' : 'files'}, {totalPages} pages
          </p>
        )}

        {organized && (
          <div role="group" aria-label="Filter documents by subject" className="flex flex-wrap gap-1.5 px-5 pb-2.5">
            <FilterChip active={subjectFilter == null} onClick={() => onSubjectFilterChange(null)}>
              All
            </FilterChip>
            {subjects.map((subject) => (
              <FilterChip key={subject} active={subjectFilter === subject} onClick={() => onSubjectFilterChange(subject)}>
                {subject}
              </FilterChip>
            ))}
            {documents.some((d) => !d.subject) && (
              <FilterChip active={subjectFilter === ''} onClick={() => onSubjectFilterChange('')}>
                No subject
              </FilterChip>
            )}
          </div>
        )}

        <div className="px-3">
          {uploads.length > 0 && (
            <ul className="mb-2 space-y-1.5" aria-label="Uploads in progress">
              {uploads.map((u) => (
                <UploadRow key={u.key} upload={u} onDismiss={() => onDismissUpload(u.key)} />
              ))}
            </ul>
          )}

          {docsStatus === 'loading' && <DocSkeleton />}

          {docsStatus === 'error' && (
            <div className="mx-2 rounded-lg bg-danger-soft p-3 text-sm text-danger">
              <p>{docsError}</p>
              <button
                onClick={onReloadDocs}
                className="mt-2 inline-flex items-center gap-1.5 font-semibold underline-offset-2 hover:underline"
              >
                <RotateCw className="size-3.5" aria-hidden="true" /> Try again
              </button>
            </div>
          )}

          {docsStatus === 'ready' && documents.length === 0 && uploads.length === 0 && (
            <p className="mx-2 rounded-lg border border-rule px-4 py-5 text-sm leading-relaxed text-ink-soft">
              No documents yet. Upload lecture notes, a textbook chapter or a reading to start asking
              questions.
            </p>
          )}

          {organized ? (
            groups.map(({ subject, docs }) => (
              <div key={subject || '-'} className="mb-2">
                <h3 className="flex items-baseline justify-between px-2 pt-1 pb-1 text-xs font-semibold text-ink-soft">
                  <span className="truncate">{subject || 'No subject'}</span>
                  <span className="shrink-0 font-normal">{docs.length}</span>
                </h3>
                <ul className="space-y-0.5">
                  {docs.map((doc) => (
                    <DocumentRow key={doc.id} doc={doc} documents={documents} onDelete={onDelete} onUpdate={onUpdateDocument} onReindex={onReindex} onDownload={onDownload} />
                  ))}
                </ul>
              </div>
            ))
          ) : (
            <ul className="space-y-0.5">
              {documents.map((doc) => (
                <DocumentRow key={doc.id} doc={doc} documents={documents} onDelete={onDelete} onUpdate={onUpdateDocument} onReindex={onReindex} onDownload={onDownload} />
              ))}
            </ul>
          )}
        </div>
        </div>

        <div className="shrink-0 border-t border-rule px-3 py-2.5">
          <button
            onClick={onOpenAccount}
            className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-paper"
            aria-label="Account and sign out"
          >
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-navy text-sm font-semibold text-white" aria-hidden="true">
              {user?.email ? user.email[0].toUpperCase() : <UserRound className="size-4" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-ink" title={user?.email}>{user?.email}</span>
              <span className="block text-xs text-ink-soft">Account · Sign out</span>
            </span>
          </button>
        </div>
      </aside>
    </>
  )
}

function TabButton({ id, tab, active, onSelect, icon: Icon, count, children }) {
  return (
    <button
      role="tab"
      id={`${id}-${tab}-tab`}
      aria-selected={active}
      aria-controls={`${id}-${tab}-panel`}
      tabIndex={active ? 0 : -1}
      onClick={() => onSelect(tab)}
      className={`flex h-9 min-w-0 items-center justify-center gap-1.5 rounded-lg px-2 text-sm font-semibold transition-colors ${
        active ? 'bg-sheet text-ink shadow-sm ring-1 ring-rule' : 'text-ink-soft hover:text-ink'
      }`}
    >
      <Icon className={`size-4 shrink-0 ${active ? 'text-navy' : ''}`} aria-hidden="true" />
      <span className="truncate">{children}</span>
      {count > 0 && (
        <span
          className={`shrink-0 rounded-full px-1.5 text-[11px] leading-[18px] font-semibold tabular-nums ${
            active ? 'bg-navy text-white' : 'bg-rule text-ink-soft'
          }`}
        >
          {count}
        </span>
      )}
    </button>
  )
}

function FilterChip({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`max-w-full truncate rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
        active ? 'border-navy bg-navy text-white' : 'border-rule bg-sheet text-ink-soft hover:border-rule-strong hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

function DocumentRow({ doc, documents, onDelete, onUpdate, onReindex, onDownload }) {
  const [confirming, setConfirming] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [editing, setEditing] = useState(false)

  async function remove() {
    setRemoving(true)
    const ok = await onDelete(doc)
    if (!ok) {
      setRemoving(false)
      setConfirming(false)
    }
  }

  if (editing) {
    return <SubjectForm doc={doc} documents={documents} onSave={onUpdate} onClose={() => setEditing(false)} />
  }

  if (confirming) {
    return (
      <li className="rounded-lg bg-danger-soft px-3 py-2.5">
        <p className="text-sm text-ink">
          Remove <span className="font-semibold break-all">{doc.filename}</span>? Skimmi will stop
          using it in answers.
        </p>
        <div className="mt-2 flex gap-2">
          <button
            onClick={remove}
            disabled={removing}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-danger px-3 text-sm font-semibold text-white hover:bg-danger/90 disabled:opacity-60"
          >
            {removing && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
            {removing ? 'Removing' : 'Remove'}
          </button>
          <button
            onClick={() => setConfirming(false)}
            disabled={removing}
            className="h-8 rounded-md px-3 text-sm font-medium text-ink-soft hover:bg-sheet disabled:opacity-60"
          >
            Cancel
          </button>
        </div>
      </li>
    )
  }

  return (
    <li className="group flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-paper">
      <span className="grid size-9 shrink-0 place-items-center rounded-md bg-paper text-navy group-hover:bg-sheet">
        <FileText className="size-4.5" aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink" title={doc.filename}>
          {doc.filename}
        </span>
        {doc.status === 'processing' ? (
          <span className="flex items-center gap-1 text-xs text-ink-soft" role="status">
            <Loader2 className="size-3 animate-spin" aria-hidden="true" /> Indexing…
          </span>
        ) : doc.status === 'failed' ? (
          <span className="block text-xs text-danger" title={doc.error}>
            Couldn’t index ·{' '}
            <button onClick={() => onReindex(doc)} className="font-semibold underline-offset-2 hover:underline">
              Retry
            </button>
          </span>
        ) : (
          <span className="block truncate text-xs text-ink-soft">
            {doc.pages} {doc.pages === 1 ? 'page' : 'pages'}
            {doc.course && <> · {doc.course}</>}
          </span>
        )}
      </span>
      <RowMenu
        label={`Options for ${doc.filename}`}
        items={[
          { label: 'Download', icon: Download, onSelect: () => onDownload(doc) },
          { label: doc.subject ? 'Change subject' : 'Add to subject', icon: Tag, onSelect: () => setEditing(true) },
          { label: 'Remove', icon: Trash2, onSelect: () => setConfirming(true), danger: true },
        ]}
      />
    </li>
  )
}

// Assign, change or remove a document's subject and course.
function SubjectForm({ doc, documents, onSave, onClose }) {
  const [subject, setSubject] = useState(doc.subject)
  const [course, setCourse] = useState(doc.course)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const id = useId()
  const subjects = subjectsOf(documents)
  const courses = coursesOf(documents, subject.trim())

  async function save(changes) {
    setSaving(true)
    setError('')
    try {
      await onSave(doc, changes)
      onClose()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  const field =
    'mt-1 h-9 w-full rounded-md border border-rule-strong bg-sheet px-2.5 text-sm text-ink outline-none placeholder:text-ink-faint focus-visible:border-navy focus-visible:outline-none disabled:bg-paper'

  return (
    <li className="rounded-lg border border-rule bg-paper px-3 py-3">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          save({ subject: subject.trim(), course: subject.trim() ? course.trim() : '' })
        }}
        onKeyDown={(e) => e.key === 'Escape' && !saving && onClose()}
      >
        <p className="truncate text-sm font-semibold text-ink" title={doc.filename}>
          {doc.filename}
        </p>
        <label className="mt-2 block text-xs font-medium text-ink-soft">
          Subject
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            list={`${id}-subjects`}
            maxLength={60}
            placeholder="e.g. Biology"
            autoFocus
            className={field}
          />
        </label>
        <datalist id={`${id}-subjects`}>
          {subjects.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <label className="mt-2 block text-xs font-medium text-ink-soft">
          Course <span className="font-normal">(optional)</span>
          <input
            value={subject.trim() ? course : ''}
            onChange={(e) => setCourse(e.target.value)}
            list={`${id}-courses`}
            maxLength={60}
            disabled={!subject.trim()}
            placeholder={subject.trim() ? 'e.g. BIO 101' : 'Add a subject first'}
            className={field}
          />
        </label>
        <datalist id={`${id}-courses`}>
          {courses.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>

        {error && (
          <p role="alert" className="mt-2 text-xs text-danger">
            {error}
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-navy px-3 text-sm font-semibold text-white hover:bg-navy-deep disabled:opacity-60"
          >
            {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
            Save
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="h-8 rounded-md px-3 text-sm font-medium text-ink-soft hover:bg-sheet disabled:opacity-60"
          >
            Cancel
          </button>
          {doc.subject && (
            <button
              type="button"
              onClick={() => save({ subject: '', course: '' })}
              disabled={saving}
              className="ml-auto h-8 rounded-md px-2 text-xs font-semibold text-danger hover:bg-danger-soft disabled:opacity-60"
            >
              Remove subject
            </button>
          )}
        </div>
      </form>
    </li>
  )
}

function UploadRow({ upload, onDismiss }) {
  if (upload.status === 'error') {
    return (
      <li className="flex gap-2.5 rounded-lg bg-danger-soft px-3 py-2.5 text-sm" role="alert">
        <AlertCircle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
        <span className="min-w-0 flex-1 text-danger">{upload.error}</span>
        <button
          onClick={onDismiss}
          className="-m-1 grid size-7 shrink-0 place-items-center rounded text-danger hover:bg-sheet"
          aria-label="Dismiss"
        >
          <X className="size-4" />
        </button>
      </li>
    )
  }

  const indexing = upload.status === 'indexing'
  const percent = Math.round(upload.progress * 100)
  return (
    <li className="rounded-lg border border-rule px-3 py-2.5">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="size-4 shrink-0 animate-spin text-navy" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate font-medium text-ink">{upload.name}</span>
      </div>
      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-paper"
        role="progressbar"
        aria-label={`Uploading ${upload.name}`}
        aria-valuenow={indexing ? 100 : percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-full rounded-full transition-[width] ${indexing ? 'w-full bg-marker' : 'bg-navy'}`}
          style={indexing ? undefined : { width: `${percent}%` }}
        />
      </div>
      <p className="mt-1.5 text-xs text-ink-soft">
        {indexing ? 'Reading and indexing pages…' : `Uploading, ${percent}%`}
      </p>
    </li>
  )
}

function DocSkeleton() {
  return (
    <ul className="space-y-1 px-2" aria-label="Loading documents">
      {[0, 1].map((i) => (
        <li key={i} className="flex items-center gap-3 py-2">
          <span className="size-9 animate-pulse rounded-md bg-paper" />
          <span className="flex-1 space-y-1.5">
            <span className="block h-3 w-3/4 animate-pulse rounded bg-paper" />
            <span className="block h-2.5 w-1/3 animate-pulse rounded bg-paper" />
          </span>
        </li>
      ))}
    </ul>
  )
}
