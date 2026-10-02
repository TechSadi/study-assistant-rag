import { useEffect, useId, useRef, useState } from 'react'
import { FileText, Info, X } from 'lucide-react'

const EXCERPT_CHARS = 320
const CLOSE_DELAY_MS = 150 // time to move the pointer from the pill onto the card

// Messages saved before excerpts were shortened only have the whole passage.
function shorten(text) {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= EXCERPT_CHARS) return flat
  return `${flat.slice(0, EXCERPT_CHARS).replace(/\s+\S*$/, '')} …`
}

const shortName = (name) => name.replace(/\.(pdf|docx|pptx|epub|txt|md|markdown)$/i, '')
const countLabel = (n) => (n === 1 ? '1 source' : `${n} sources`)

// The passages an answer was based on, as one quiet pill: the first reference
// and a count. Hovering (or tapping, on touch screens) lists every reference;
// picking one opens its excerpt below the answer.
export default function Sources({ sources }) {
  const [peek, setPeek] = useState(false)
  const [open, setOpen] = useState(false)
  const [focused, setFocused] = useState(null)
  const baseId = useId()
  const wrapper = useRef(null)
  const closeTimer = useRef(null)
  const cards = useRef([])

  // Bring the excerpt picked from a citation into view (it can open below the fold).
  useEffect(() => {
    if (open && focused !== null) cards.current[focused]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [open, focused])

  // The list closes on a click or tap elsewhere, or on Escape.
  useEffect(() => {
    if (!peek) return
    const onPointer = (e) => wrapper.current?.contains(e.target) || setPeek(false)
    const onKey = (e) => e.key === 'Escape' && setPeek(false)
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [peek])

  useEffect(() => () => clearTimeout(closeTimer.current), [])

  if (!sources?.length) return null
  const [first] = sources
  const more = sources.length - 1

  function showList(e) {
    if (e.pointerType !== 'mouse') return // touch opens it with a tap instead
    clearTimeout(closeTimer.current)
    setPeek(true)
  }

  function hideList(e) {
    if (e.pointerType !== 'mouse') return
    closeTimer.current = setTimeout(() => setPeek(false), CLOSE_DELAY_MS)
  }

  function openAt(i) {
    setPeek(false)
    setOpen(true)
    setFocused(i)
  }

  return (
    <div className="mt-4">
      <div
        ref={wrapper}
        className="relative inline-block max-w-full"
        onPointerEnter={showList}
        onPointerLeave={hideList}
        onBlur={(e) => wrapper.current?.contains(e.relatedTarget) || setPeek(false)}
      >
        <button
          onClick={() => setPeek(!peek)}
          aria-expanded={peek}
          aria-controls={`${baseId}-list`}
          aria-label={`${countLabel(sources.length)}: ${first.source}, page ${first.page}${more ? ` and ${more} more` : ''}`}
          className={`inline-flex max-w-full items-center gap-1.5 rounded-full border py-1 pr-2.5 pl-2 text-xs transition-colors ${
            peek || open ? 'border-rule-strong bg-sheet' : 'border-rule bg-sheet/70 hover:border-rule-strong hover:bg-sheet'
          }`}
        >
          <FileText className="size-3.5 shrink-0 text-ink-faint" aria-hidden="true" />
          <span className="min-w-0 truncate font-medium text-ink">{shortName(first.source)}</span>
          <span className="shrink-0 text-ink-faint">p. {first.page}</span>
          {more > 0 && <span className="shrink-0 font-semibold text-ink-soft">+{more}</span>}
        </button>

        {peek && (
          // pb-2 bridges the gap to the pill, so the pointer can cross it without closing the list.
          <div className="absolute bottom-full left-0 z-30 w-[min(22rem,calc(100vw-5rem))] pb-2">
            <div
              id={`${baseId}-list`}
              className="rounded-xl border border-rule bg-sheet p-1.5 shadow-[0_12px_32px_-12px_rgb(22_35_63/0.35)]"
            >
              <p className="px-2.5 pt-1.5 pb-1 text-[0.7rem] font-semibold tracking-wide text-ink-faint uppercase">
                {countLabel(sources.length)}
              </p>
              <ol className="max-h-72 overflow-y-auto">
                {sources.map((s, i) => (
                  <li key={`${s.doc_id ?? s.source}-${s.page}`}>
                    <button
                      onClick={() => openAt(i)}
                      aria-label={`Show the excerpt from ${s.source}, page ${s.page}`}
                      className="flex w-full gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-paper focus-visible:bg-paper"
                    >
                      <span className="mt-px grid size-5 shrink-0 place-items-center rounded-full bg-paper text-[0.7rem] font-semibold text-ink-soft">
                        {i + 1}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline gap-2 text-xs">
                          <span className="min-w-0 truncate font-semibold text-ink">{s.source}</span>
                          <span className="shrink-0 text-ink-faint">page {s.page}</span>
                        </span>
                        {(s.excerpt || s.text) && (
                          <span className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-ink-soft">
                            {(s.excerpt || s.text).replace(/\s+/g, ' ').replace(/^… /, '')}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
              <button
                onClick={() => openAt(null)}
                className="mt-1 w-full rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-navy hover:bg-paper"
              >
                Show all excerpts
              </button>
            </div>
          </div>
        )}
      </div>

      {open && (
        <div id={`${baseId}-excerpts`} className="mt-3">
          <div className="flex items-start justify-between gap-3">
            <p className="flex gap-1.5 text-xs leading-relaxed text-ink-soft">
              <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
              <span>
                These passages were retrieved from your documents to support this answer. They show where to check
                it, not proof that every sentence in it is correct.
              </span>
            </p>
            <button
              onClick={() => {
                setOpen(false)
                setFocused(null)
              }}
              aria-label="Hide excerpts"
              className="grid size-6 shrink-0 place-items-center rounded-md text-ink-soft hover:bg-paper hover:text-ink"
            >
              <X className="size-3.5" aria-hidden="true" />
            </button>
          </div>
          <ol className="mt-2 space-y-2">
            {sources.map((s, i) => (
              <li key={i} ref={(el) => (cards.current[i] = el)} className="scroll-mt-4">
                <Excerpt source={s} highlighted={focused === i} />
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

function Excerpt({ source: s, highlighted }) {
  const [full, setFull] = useState(false)
  const passage = (s.text || s.excerpt || '').replace(/\n{3,}/g, '\n\n').trim()
  const excerpt = s.text ? s.excerpt : shorten(passage)
  const longer = passage.replace(/\s+/g, ' ').length > excerpt.replace(/ …$|^… /g, '').length

  return (
    <figure
      className={`rounded-lg border bg-sheet px-4 py-3 transition-colors ${
        highlighted ? 'border-ink/25 shadow-[inset_3px_0_0_var(--color-marker)]' : 'border-rule'
      }`}
    >
      <figcaption className="text-xs text-ink-soft">
        <span className="font-semibold text-ink [overflow-wrap:anywhere]">{s.source}</span>, page {s.page}
      </figcaption>
      {passage ? (
        <blockquote className="mt-2 border-l-[3px] border-marker pl-3 font-serif text-[0.95rem] leading-relaxed whitespace-pre-line text-ink">
          {full ? passage : excerpt}
        </blockquote>
      ) : (
        <p className="mt-2 text-sm text-ink-soft italic">The text of this passage isn't available.</p>
      )}
      {longer && (
        <button
          onClick={() => setFull(!full)}
          aria-expanded={full}
          className="mt-2 text-xs font-semibold text-navy underline-offset-2 hover:underline"
        >
          {full ? 'Show less' : 'Show the whole passage'}
        </button>
      )}
    </figure>
  )
}
