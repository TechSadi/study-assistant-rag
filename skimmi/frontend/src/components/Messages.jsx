import Markdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { AlertCircle, RotateCw } from 'lucide-react'
import { LogoMark } from './Logo'
import Sources from './Sources'
import { modeById } from '../modes'

// The mode and material a question was asked with, e.g. "Quiz · notes.pdf · pages 3–7".
export function UserMessage({ content, mode = 'ask', scope = '' }) {
  const { label, icon: Icon } = modeById(mode)
  return (
    <div className="flex flex-col items-end">
      <p className="max-w-[85%] rounded-2xl rounded-br-md bg-navy px-4 py-2.5 text-[0.95rem] leading-relaxed whitespace-pre-wrap text-white [overflow-wrap:anywhere]">
        {content}
      </p>
      {(mode !== 'ask' || scope) && (
        <p className="mt-1.5 flex max-w-[85%] items-center gap-1.5 text-xs text-ink-soft">
          <Icon className="size-3.5 shrink-0 text-navy" aria-hidden="true" />
          <span className="font-semibold text-ink">{label}</span>
          {scope && <span className="truncate" title={scope}>· {scope}</span>}
        </p>
      )}
    </div>
  )
}

// Markdown (with math) for text inside quiz questions and flashcards.
// Paragraphs become block spans so it can sit inside a button.
const blockSpans = { p: ({ children }) => <span className="block">{children}</span> }

export function RichText({ children, className = '' }) {
  return (
    <span className={`answer block ${className}`}>
      <Markdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={blockSpans}>
        {children}
      </Markdown>
    </span>
  )
}

export function AssistantFrame({ children }) {
  return (
    <div className="flex gap-3 sm:gap-4">
      <LogoMark className="mt-0.5 size-7 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

export function AssistantMessage({ content, sources }) {
  return (
    <AssistantFrame>
      <div className="answer">
        <Markdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
          {content}
        </Markdown>
      </div>
      <Sources sources={sources} />
    </AssistantFrame>
  )
}

export function ThinkingMessage({ mode = 'ask' }) {
  const { working } = modeById(mode)
  const study = !modeById(mode).needsText
  return (
    <AssistantFrame>
      <div role="status" className="inline-flex items-center gap-1.5 rounded-2xl rounded-tl-md border border-rule bg-sheet px-4 py-3.5">
        {study ? (
          <span className="mr-1.5 text-sm text-ink-soft">{working}…</span>
        ) : (
          <span className="sr-only">{working}</span>
        )}
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="animate-typing size-2 rounded-full bg-navy"
            style={{ animationDelay: `${delay}ms` }}
            aria-hidden="true"
          />
        ))}
      </div>
    </AssistantFrame>
  )
}

export function ErrorMessage({ content, onRetry, retrying }) {
  return (
    <AssistantFrame>
      <div role="alert" className="flex gap-3 rounded-xl border border-danger/20 bg-danger-soft px-4 py-3">
        <AlertCircle className="mt-0.5 size-4.5 shrink-0 text-danger" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-semibold text-danger">Couldn't get an answer</p>
          <p className="mt-0.5 text-sm text-ink">{content}</p>
          <button
            onClick={onRetry}
            disabled={retrying}
            className="mt-2 inline-flex items-center gap-1.5 text-sm font-semibold text-navy underline-offset-2 hover:underline disabled:opacity-50"
          >
            <RotateCw className="size-3.5" aria-hidden="true" /> Ask again
          </button>
        </div>
      </div>
    </AssistantFrame>
  )
}
