import { useEffect, useRef } from 'react'
import { ChartNoAxesColumnIncreasing, Loader2, RotateCw, X } from 'lucide-react'
import { formatWhen } from '../dates'
import { MODES } from '../modes'

const percentOf = (progress) => Math.round((progress?.accuracy ?? 0) * 100)

// What each count in progress.activity means, by mode id.
const ACTIVITY_LABELS = {
  ask: ['question asked', 'questions asked'],
  explain: ['explanation', 'explanations'],
  summarize: ['summary', 'summaries'],
  quiz: ['quiz made', 'quizzes made'],
  flashcards: ['flashcard set', 'flashcard sets'],
}

// The top-bar button (next to New chat) that opens the study dashboard.
export function ProgressButton({ onClick }) {
  return (
    <button
      onClick={onClick}
      title="Study progress"
      aria-label="Study progress"
      aria-haspopup="dialog"
      className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-rule-strong bg-sheet px-3 text-sm font-semibold text-ink transition-colors hover:border-navy"
    >
      <ChartNoAxesColumnIncreasing className="size-4" aria-hidden="true" />
      <span className="hidden sm:inline">Progress</span>
    </button>
  )
}

// Quiz scores, quiz history and study activity, from GET /api/progress.
export default function StudyDashboard({ progress, status, onReload, onOpenConversation, onClose }) {
  const panel = useRef(null)

  useEffect(() => {
    panel.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/30 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <section
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dashboard-title"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90dvh] w-full overflow-y-auto rounded-t-2xl bg-sheet p-6 shadow-xl outline-none sm:max-w-xl sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id="dashboard-title" className="font-serif text-2xl font-medium text-ink">Study progress</h2>
            <p className="mt-1 text-sm text-ink-soft">Your quiz scores and what you've studied so far.</p>
          </div>
          <button onClick={onClose} className="-mt-1 -mr-2 grid size-10 place-items-center rounded-lg text-ink-soft hover:bg-paper" aria-label="Close">
            <X className="size-5" />
          </button>
        </div>

        {status === 'loading' && !progress && (
          <p className="mt-5 flex items-center gap-2 text-sm text-ink-soft" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading…
          </p>
        )}

        {status === 'error' && !progress && (
          <p className="mt-5 text-sm text-danger">
            Couldn't load your progress.{' '}
            <button onClick={onReload} className="inline-flex items-center gap-1 font-semibold underline-offset-2 hover:underline">
              <RotateCw className="size-3.5" aria-hidden="true" /> Try again
            </button>
          </p>
        )}

        {progress && (
          <>
            <Scores progress={progress} />
            <Activity activity={progress.activity} />
            <QuizHistory recent={progress.recent} onOpenConversation={onOpenConversation} />
          </>
        )}
      </section>
    </div>
  )
}

function Scores({ progress }) {
  if (!progress.quizzes_completed) {
    return (
      <div className="mt-5 rounded-xl border border-rule bg-paper/60 p-4 text-sm text-ink-soft">
        Finish a quiz and your scores will be tracked here.
      </div>
    )
  }
  const percent = percentOf(progress)
  return (
    <div className="mt-5 rounded-xl border border-rule bg-paper/60 p-4">
      <dl className="grid grid-cols-3 gap-2">
        <Stat label="Quizzes" value={progress.quizzes_completed} />
        <Stat label="Answered" value={progress.questions_answered} />
        <Stat label="Accuracy" value={`${percent}%`} />
      </dl>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-rule" aria-hidden="true">
        <div className="h-full rounded-full bg-ok" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-1.5 text-xs text-ink-soft">
        {progress.correct_answers} of {progress.questions_answered} answers correct
      </p>
    </div>
  )
}

function Activity({ activity }) {
  return (
    <section aria-labelledby="activity-heading" className="mt-6">
      <h3 id="activity-heading" className="text-sm font-semibold text-ink">Learning activity</h3>
      <ul className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {MODES.map(({ id, label, icon: Icon }) => {
          const n = activity?.[id] ?? 0
          const [one, many] = ACTIVITY_LABELS[id] ?? [label, label]
          return (
            <li key={id} className="flex items-center gap-2.5 rounded-lg border border-rule px-3 py-2">
              <Icon className="size-4 shrink-0 text-navy" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block text-base leading-tight font-semibold text-ink tabular-nums">{n}</span>
                <span className="block truncate text-[0.7rem] text-ink-soft">{n === 1 ? one : many}</span>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function QuizHistory({ recent, onOpenConversation }) {
  return (
    <section aria-labelledby="history-heading" className="mt-6">
      <h3 id="history-heading" className="text-sm font-semibold text-ink">Quiz history</h3>
      {recent.length === 0 ? (
        <p className="mt-1.5 text-sm text-ink-soft">No quizzes finished yet. Choose Quiz mode in the chat box to start one.</p>
      ) : (
        <ul className="mt-1.5 space-y-0.5">
          {recent.map((a) => (
            <li key={a.id}>
              <RecentAttempt attempt={a} onOpen={a.conversation_id ? () => onOpenConversation(a.conversation_id) : null} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Stat({ label, value }) {
  return (
    <div className="rounded-lg bg-sheet px-2.5 py-2">
      <dt className="text-xs font-medium text-ink-soft">{label}</dt>
      <dd className="text-lg leading-tight font-semibold text-ink tabular-nums">{value}</dd>
    </div>
  )
}

function RecentAttempt({ attempt: a, onOpen }) {
  const good = a.total && a.correct / a.total >= 0.8
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink" title={a.title}>
          {a.title}
        </span>
        <span className="block truncate text-xs text-ink-soft">
          {formatWhen(a.completed_at)}
          {!onOpen && ' · chat deleted'}
        </span>
      </span>
      <span
        className={`shrink-0 rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums ${
          good ? 'bg-ok-soft text-ok' : 'bg-paper text-ink'
        }`}
      >
        {a.correct}/{a.total}
      </span>
    </>
  )
  const row = 'flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left'
  return onOpen ? (
    <button onClick={onOpen} className={`${row} hover:bg-paper`} aria-label={`Open ${a.title}, scored ${a.correct} of ${a.total}`}>
      {body}
    </button>
  ) : (
    <div className={row}>{body}</div>
  )
}
