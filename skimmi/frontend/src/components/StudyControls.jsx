import { useEffect } from 'react'
import { Check, ChevronDown, FileText, Target, X } from 'lucide-react'
import { MODES, modeById } from '../modes'
import { groupBySubject, subjectsOf } from '../subjects'
import useMenu from '../useMenu'

// The material picker's value: '' (all documents), 'doc:<id>' or 'subject:<name>'.
const pickerValue = (scope) => (scope.docId ? `doc:${scope.docId}` : scope.subject ? `subject:${scope.subject}` : '')

const EMPTY_SCOPE = { docId: null, subject: null, topic: '', pageFrom: '', pageTo: '' }

const chipClass =
  'inline-flex h-8 min-w-0 items-center gap-1.5 rounded-lg px-2.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:text-ink-faint'

// The menus open upward from the composer card (its nearest positioned ancestor).
const panelClass =
  'absolute bottom-full left-0 z-30 mb-2 w-full max-w-sm rounded-xl border border-rule bg-sheet p-1.5 shadow-[0_12px_32px_-12px_rgb(22_35_63/0.35)]'

// Composer toolbar chip: which study mode the next message uses.
export function ModeMenu({ mode, onModeChange, disabled }) {
  const { open, setOpen, close, button, panel } = useMenu()
  const current = modeById(mode)
  const Icon = current.icon

  // Start keyboard users on the current mode.
  useEffect(() => {
    if (open) panel.current?.querySelector('[aria-checked="true"]')?.focus()
  }, [open, panel])

  function onKeyDown(e) {
    const items = [...panel.current.querySelectorAll('[role="menuitemradio"]')]
    const i = items.indexOf(document.activeElement)
    const next = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[e.key]
    if (next !== undefined) {
      e.preventDefault()
      items[(next + items.length) % items.length].focus()
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <>
      <button
        ref={button}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Study mode: ${current.label}`}
        className={`${chipClass} shrink-0 ${open ? 'bg-navy text-white' : 'bg-paper text-ink hover:bg-rule'}`}
      >
        <Icon className={`size-4 ${open ? 'text-marker' : 'text-navy'}`} aria-hidden="true" />
        {current.label}
        <ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>

      {open && (
        <div ref={panel} role="menu" aria-label="Study mode" onKeyDown={onKeyDown} className={panelClass}>
          <p className="px-2.5 pt-1.5 pb-2 text-xs font-medium text-ink-faint">Choose a study mode</p>
          {MODES.map((m) => {
            const active = m.id === mode
            const ItemIcon = m.icon
            return (
              <button
                key={m.id}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => {
                  onModeChange(m.id)
                  close()
                }}
                className={`flex w-full items-start gap-3 rounded-lg px-2.5 py-2 text-left outline-none focus-visible:outline-none ${
                  active ? 'bg-navy text-white' : 'text-ink hover:bg-paper focus-visible:bg-paper'
                }`}
              >
                <ItemIcon className={`mt-0.5 size-4 shrink-0 ${active ? 'text-marker' : 'text-navy'}`} aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{m.label}</span>
                  <span className={`block text-xs ${active ? 'text-white/75' : 'text-ink-soft'}`}>{m.hint}</span>
                </span>
                {active && <Check className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
              </button>
            )
          })}
        </div>
      )}
    </>
  )
}

// Composer toolbar chip: what material the next message studies.
// `scope` is { docId, subject, topic, pageFrom, pageTo }; page numbers are strings as typed.
export function ScopeMenu({ scope, onScopeChange, documents, disabled }) {
  const { open, setOpen, close, button, panel } = useMenu()
  const doc = documents.find((d) => d.id === scope.docId)
  const hasScope = scope.docId || scope.subject || scope.topic || scope.pageFrom || scope.pageTo
  const set = (changes) => onScopeChange({ ...scope, ...changes })
  const organized = subjectsOf(documents).length > 0

  useEffect(() => {
    if (open) panel.current?.querySelector('select')?.focus()
  }, [open, panel])

  function pick(value) {
    const [kind, ...rest] = value.split(':')
    const id = rest.join(':')
    set({ docId: kind === 'doc' ? id : null, subject: kind === 'subject' ? id : null, pageFrom: '', pageTo: '' })
  }

  const label = doc ? doc.filename : scope.subject || 'All documents'
  const pages = doc && (scope.pageFrom || scope.pageTo) ? `p. ${scope.pageFrom || 1}–${scope.pageTo || doc.pages}` : ''

  return (
    <>
      <button
        ref={button}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Study material: ${label}${pages ? `, ${pages}` : ''}${scope.topic ? `, topic ${scope.topic}` : ''}`}
        title={label}
        className={`${chipClass} ${
          open ? 'bg-navy text-white' : hasScope ? 'bg-marker-soft text-ink hover:bg-marker' : 'bg-paper text-ink hover:bg-rule'
        }`}
      >
        <FileText className={`size-4 shrink-0 ${open ? 'text-marker' : 'text-navy'}`} aria-hidden="true" />
        <span className="max-w-[9rem] truncate sm:max-w-[16rem]">{label}</span>
        {pages && <span className="shrink-0 font-medium opacity-75">{pages}</span>}
        {scope.topic && <Target className="size-3.5 shrink-0" aria-hidden="true" />}
        <ChevronDown className={`size-3.5 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>

      {open && (
        <div
          ref={panel}
          role="dialog"
          aria-label="Study material"
          // The panel sits inside the composer form: Enter in a field closes it instead of sending.
          onKeyDown={(e) => {
            if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
              e.preventDefault()
              close()
            }
          }}
          className={`${panelClass} space-y-2.5 p-3`}>
          <p className="text-xs font-medium text-ink-faint">What should Skimmi study?</p>

          <label className="relative block">
            <span className="sr-only">Document or subject to study</span>
            <FileText className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-navy" aria-hidden="true" />
            <select
              value={pickerValue(scope)}
              onChange={(e) => pick(e.target.value)}
              className="h-9 w-full appearance-none truncate rounded-lg border border-rule bg-sheet pr-8 pl-8 text-sm text-ink hover:border-rule-strong focus-visible:border-navy"
            >
              <option value="">All documents</option>
              {organized
                ? groupBySubject(documents).map(({ subject, docs }) => (
                    <optgroup key={subject || '-'} label={subject || 'No subject'}>
                      {subject && (
                        <option value={`subject:${subject}`}>
                          All of {subject} ({docs.length} {docs.length === 1 ? 'document' : 'documents'})
                        </option>
                      )}
                      {docs.map((d) => (
                        <option key={d.id} value={`doc:${d.id}`}>
                          {d.course ? `${d.filename} · ${d.course}` : d.filename}
                        </option>
                      ))}
                    </optgroup>
                  ))
                : documents.map((d) => (
                    <option key={d.id} value={`doc:${d.id}`}>
                      {d.filename}
                    </option>
                  ))}
            </select>
            <ChevronDown className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-ink-soft" aria-hidden="true" />
          </label>

          {doc && (
            <fieldset className="flex h-9 items-center gap-1 rounded-lg border border-rule bg-sheet px-2.5 text-sm text-ink-soft focus-within:border-navy">
              <legend className="sr-only">Pages of {doc.filename} (optional)</legend>
              <span aria-hidden="true">Pages</span>
              <PageInput label="First page" value={scope.pageFrom} max={doc.pages} onChange={(v) => set({ pageFrom: v })} placeholder="1" />
              <span aria-hidden="true">–</span>
              <PageInput label="Last page" value={scope.pageTo} max={doc.pages} onChange={(v) => set({ pageTo: v })} placeholder={String(doc.pages)} />
              <span className="ml-auto text-xs text-ink-faint">optional</span>
            </fieldset>
          )}

          <label className="relative block">
            <span className="sr-only">Topic or chapter (optional)</span>
            <Target className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-navy" aria-hidden="true" />
            <input
              value={scope.topic}
              maxLength={200}
              onChange={(e) => set({ topic: e.target.value })}
              placeholder="Topic or chapter (optional)"
              className="h-9 w-full rounded-lg border border-rule bg-sheet pr-3 pl-8 text-sm text-ink outline-none placeholder:text-ink-faint hover:border-rule-strong focus-visible:border-navy focus-visible:outline-none"
            />
          </label>

          <div className="flex items-center justify-between pt-0.5">
            <button
              type="button"
              onClick={() => onScopeChange(EMPTY_SCOPE)}
              disabled={!hasScope}
              className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-sm font-medium text-ink-soft hover:bg-paper hover:text-ink disabled:invisible"
            >
              <X className="size-3.5" aria-hidden="true" /> Clear
            </button>
            <button
              type="button"
              onClick={close}
              className="h-8 rounded-lg bg-navy px-3 text-sm font-semibold text-white hover:bg-navy-deep"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </>
  )
}

function PageInput({ label, value, max, onChange, placeholder }) {
  return (
    <input
      aria-label={label}
      inputMode="numeric"
      value={value}
      placeholder={placeholder}
      onChange={(e) => {
        const digits = e.target.value.replace(/\D/g, '').slice(0, 5)
        onChange(digits && Number(digits) > max ? String(max) : digits)
      }}
      className="w-10 bg-transparent text-center text-ink outline-none placeholder:text-ink-faint focus-visible:outline-none"
    />
  )
}
