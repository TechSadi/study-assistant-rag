import { useEffect, useLayoutEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { MoreHorizontal } from 'lucide-react'
import useMenu from '../useMenu'

// The "⋯" button on a sidebar row and its menu of actions.
// `items` is [{ label, icon, onSelect, danger? }].
// The panel is portalled to <body> and fixed-positioned: the sidebar is transformed
// (which would make it the containing block for `fixed`) and scrolls (which would clip it).
// It opens upward when there isn't room below the button.
export default function RowMenu({ label, items }) {
  const { open, setOpen, close, button, panel } = useMenu()
  const [position, setPosition] = useState(null)

  useLayoutEffect(() => {
    if (!open) return
    const rect = button.current.getBoundingClientRect()
    const height = panel.current.offsetHeight
    const below = rect.bottom + 4 + height <= window.innerHeight - 8
    setPosition({
      top: below ? rect.bottom + 4 : Math.max(8, rect.top - 4 - height),
      right: Math.max(8, window.innerWidth - rect.right),
    })
  }, [open, button, panel])

  // A fixed panel would drift away from its row, so close it when anything scrolls or resizes.
  useEffect(() => {
    if (!open) return
    const dismiss = () => setOpen(false)
    window.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    return () => {
      window.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [open, setOpen])

  useEffect(() => {
    if (open && position) panel.current?.querySelector('[role="menuitem"]')?.focus()
  }, [open, position, panel])

  function onKeyDown(e) {
    const entries = [...panel.current.querySelectorAll('[role="menuitem"]')]
    const i = entries.indexOf(document.activeElement)
    const next = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: entries.length - 1 }[e.key]
    if (next !== undefined) {
      e.preventDefault()
      entries[(next + entries.length) % entries.length].focus()
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <>
      <button
        ref={button}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title="More options"
        className={`grid size-8 shrink-0 place-items-center rounded-md transition-colors ${
          open ? 'bg-sheet text-navy' : 'text-ink-faint hover:bg-sheet hover:text-navy'
        }`}
      >
        <MoreHorizontal className="size-4" aria-hidden="true" />
      </button>

      {open && createPortal(
        <div
          ref={panel}
          role="menu"
          aria-label={label}
          onKeyDown={onKeyDown}
          style={position ?? { visibility: 'hidden', top: 0, right: 0 }}
          className="fixed z-50 min-w-44 rounded-xl border border-rule bg-sheet p-1.5 shadow-[0_12px_32px_-12px_rgb(22_35_63/0.35)]"
        >
          {items.map(({ label: itemLabel, icon: Icon, onSelect, danger }) => (
            <button
              key={itemLabel}
              type="button"
              role="menuitem"
              onClick={() => {
                close()
                onSelect()
              }}
              className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm font-medium outline-none focus-visible:outline-none ${
                danger
                  ? 'text-danger hover:bg-danger-soft focus-visible:bg-danger-soft'
                  : 'text-ink hover:bg-paper focus-visible:bg-paper'
              }`}
            >
              <Icon className={`size-4 shrink-0 ${danger ? '' : 'text-navy'}`} aria-hidden="true" />
              {itemLabel}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
