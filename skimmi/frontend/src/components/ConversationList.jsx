import { useState } from 'react'
import { Loader2, Trash2 } from 'lucide-react'
import { formatWhen } from '../dates'
import RowMenu from './RowMenu'

export default function ConversationList({ conversations, status, activeId, onOpen, onDelete }) {
  return (
    <div className="px-3">
      {status === 'loading' && <p className="px-2 py-2 text-sm text-ink-soft">Loading chats…</p>}
      {status === 'error' && (
        <p className="mx-2 rounded-lg bg-danger-soft p-3 text-sm text-danger">
          Couldn't load your chats. Refresh the page to try again.
        </p>
      )}
      {status === 'ready' && conversations.length === 0 && (
        <p className="px-2 py-1 text-sm leading-relaxed text-ink-soft">
          Your chats will appear here after you ask a question.
        </p>
      )}
      <ul className="space-y-0.5">
        {conversations.map((c) => (
          <ConversationRow
            key={c.id}
            conversation={c}
            active={c.id === activeId}
            onOpen={() => onOpen(c.id)}
            onDelete={() => onDelete(c)}
          />
        ))}
      </ul>
    </div>
  )
}

function ConversationRow({ conversation, active, onOpen, onDelete }) {
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)

  async function remove() {
    setDeleting(true)
    const ok = await onDelete()
    if (!ok) {
      setDeleting(false)
      setConfirming(false)
    }
  }

  if (confirming) {
    return (
      <li className="rounded-lg bg-danger-soft px-3 py-2.5">
        <p className="text-sm text-ink">
          Delete <span className="font-semibold [overflow-wrap:anywhere]">{conversation.title}</span>? This
          can't be undone.
        </p>
        <div className="mt-2 flex gap-2">
          <button
            onClick={remove}
            disabled={deleting}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-danger px-3 text-sm font-semibold text-white hover:bg-danger/90 disabled:opacity-60"
          >
            {deleting && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
            {deleting ? 'Deleting' : 'Delete'}
          </button>
          <button
            onClick={() => setConfirming(false)}
            disabled={deleting}
            className="h-8 rounded-md px-3 text-sm font-medium text-ink-soft hover:bg-sheet disabled:opacity-60"
          >
            Cancel
          </button>
        </div>
      </li>
    )
  }

  return (
    <li
      className={`group flex items-center rounded-lg ${
        active ? 'bg-paper shadow-[inset_3px_0_0_var(--color-navy)]' : 'hover:bg-paper'
      }`}
    >
      <button
        onClick={onOpen}
        aria-current={active ? 'page' : undefined}
        className="min-w-0 flex-1 py-2 pr-1 pl-3 text-left"
      >
        <span
          className={`block truncate text-sm text-ink ${active ? 'font-semibold' : 'font-medium'}`}
          title={conversation.title}
        >
          {conversation.title}
        </span>
        <span className="block text-xs text-ink-soft">{formatWhen(conversation.updated_at)}</span>
      </button>
      <span className="mr-1">
        <RowMenu
          label={`Options for chat ${conversation.title}`}
          items={[{ label: 'Delete chat', icon: Trash2, onSelect: () => setConfirming(true), danger: true }]}
        />
      </span>
    </li>
  )
}
