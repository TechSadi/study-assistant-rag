import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Loader2 } from 'lucide-react'

const MAX_LENGTH = 2000

// `mode` sets the placeholder and hint. Modes that don't need text can be sent empty.
// `tools` (the mode and material menus) sit in the toolbar beside the send button.
export default function Composer({ onSend, busy, disabled, disabledReason, mode, tools }) {
  const [text, setText] = useState('')
  const ref = useRef(null)

  // Grow with the text, up to about eight lines.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 208)}px`
  }, [text])

  const canSend = !busy && !disabled && (!mode.needsText || text.trim().length > 0)

  function submit(e) {
    e?.preventDefault()
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) submit(e)
  }

  return (
    <form onSubmit={submit} className="mx-auto w-full max-w-3xl">
      <div
        className={`relative rounded-2xl border bg-sheet p-2 shadow-[0_2px_10px_-4px_rgb(22_35_63/0.12)] transition-colors focus-within:border-navy ${
          disabled ? 'border-rule' : 'border-rule-strong'
        }`}
      >
        <label htmlFor="question" className="sr-only">
          {mode.placeholder}
        </label>
        <textarea
          id="question"
          ref={ref}
          rows={1}
          value={text}
          maxLength={MAX_LENGTH}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
          placeholder={disabled ? disabledReason : mode.placeholder}
          className="block max-h-52 min-h-10 w-full resize-none bg-transparent px-2 py-2 text-base leading-6 text-ink outline-none placeholder:text-ink-faint focus-visible:outline-none disabled:cursor-not-allowed"
        />
        <div className="mt-1 flex items-center gap-1.5">
          <div className="flex min-w-0 flex-1 items-center gap-1.5">{tools}</div>
          <button
            type="submit"
            disabled={!canSend}
            aria-label={busy ? 'Waiting for answer' : mode.needsText ? 'Send question' : mode.label}
            className="grid size-9 shrink-0 place-items-center rounded-xl bg-navy text-white transition-colors hover:bg-navy-deep disabled:bg-rule disabled:text-ink-faint"
          >
            {busy ? (
              <Loader2 className="size-4.5 animate-spin" aria-hidden="true" />
            ) : (
              <ArrowUp className="size-4.5" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
      <p className="mt-2 px-1 text-center text-xs text-ink-soft">
        <span className="font-semibold text-ink">{mode.label}:</span> {mode.hint} Only your uploaded documents are used.
      </p>
    </form>
  )
}
