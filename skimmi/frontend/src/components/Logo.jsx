export function LogoMark({ className = 'size-8' }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#1d3461" />
      <rect x="6" y="17" width="20" height="6" rx="2" fill="#f7e15a" transform="rotate(-8 16 20)" />
      <path d="M9 11h14M9 15h10" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-lg font-semibold tracking-tight text-ink">
        Skimmi <span className="font-normal text-ink-soft">AI</span>
      </span>
    </div>
  )
}
