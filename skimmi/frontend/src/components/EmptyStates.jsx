import { Upload } from 'lucide-react'

// A sketch of what Skimmi does: find the passage, highlight it, cite the page.
function CitedPage() {
  return (
    <div className="relative mx-auto h-44 w-36 sm:h-48 sm:w-40" aria-hidden="true">
      <div className="absolute inset-0 translate-x-3 translate-y-2 rotate-3 rounded-lg border border-rule bg-sheet" />
      <div className="absolute inset-0 -rotate-2 rounded-lg border border-rule-strong bg-sheet p-4 shadow-[0_12px_28px_-16px_rgb(22_35_63/0.35)]">
        <div className="space-y-2.5">
          <div className="h-2 w-2/3 rounded bg-ink/80" />
          <div className="h-1.5 w-full rounded bg-rule" />
          <div className="h-1.5 w-11/12 rounded bg-rule" />
          <div className="h-1.5 w-full rounded bg-rule" />
          <div className="-mx-1 rounded-sm bg-marker px-1 py-[3px]">
            <div className="h-1.5 w-10/12 rounded bg-ink/35" />
          </div>
          <div className="h-1.5 w-full rounded bg-rule" />
          <div className="h-1.5 w-4/5 rounded bg-rule" />
          <div className="h-1.5 w-full rounded bg-rule" />
        </div>
        <span className="absolute right-3 bottom-2.5 text-[0.65rem] font-semibold text-ink-soft">p. 12</span>
      </div>
    </div>
  )
}

export function NoDocumentsState({ onUploadClick }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-2 py-10 text-center">
      <CitedPage />
      <h1 className="mt-10 font-serif text-3xl leading-tight font-medium text-balance text-ink sm:text-[2.1rem]">
        Upload a document to start studying
      </h1>
      <p className="mt-3 leading-relaxed text-pretty text-ink-soft">
        Add lecture notes, a textbook chapter or a reading. Skimmi answers your questions from those
        pages and shows the page each answer came from.
      </p>
      <button
        onClick={onUploadClick}
        className="mt-7 inline-flex h-11 items-center gap-2 rounded-xl bg-navy px-5 font-semibold text-white transition-colors hover:bg-navy-deep"
      >
        <Upload className="size-4" aria-hidden="true" /> Upload a document
      </button>
      <p className="mt-3 text-xs text-ink-soft">PDF, Word, PowerPoint, EPUB, text or Markdown, up to 25 MB. You can also drop files here.</p>
    </div>
  )
}

// What each mode offers on a new chat. The study modes' first option sends no
// text, so it covers the whole selected document.
const STARTERS = {
  ask: {
    heading: 'What do you want to understand?',
    suggestions: (name) => [
      name ? `Summarize the main ideas in ${name}` : 'Summarize the main ideas across my documents',
      'List the key terms and what they mean',
      'What would be good exam questions on this material?',
    ],
  },
  explain: {
    heading: 'What should Skimmi explain?',
    suggestions: () => [
      'Explain the main idea as if I were new to the subject',
      'Explain the hardest concept here step by step, with an example',
      'Explain how the key ideas connect to each other',
    ],
  },
  summarize: {
    heading: 'Get the key points',
    suggestions: (name) => [
      '',
      `Summarize ${name ?? 'my documents'} in five bullet points`,
      'Summarize the definitions and formulas I need to remember',
    ],
  },
  quiz: {
    heading: 'Test yourself',
    suggestions: () => ['', 'Quiz me on the key definitions', 'Make 10 harder questions'],
  },
  flashcards: {
    heading: 'Revise with flashcards',
    suggestions: () => ['', 'Make flashcards for the key terms and their definitions', 'Make 12 flashcards'],
  },
}

export function NewConversationState({ documents, selectedDoc, subject, subjectDocs, mode, onPick }) {
  const single = documents.length === 1
  const name = selectedDoc?.filename ?? subject ?? (single ? documents[0].filename : null)
  const starter = STARTERS[mode.id]
  const suggestions = starter.suggestions(name)
  const target = name ?? 'all my documents'
  const label = (s) =>
    s ||
    { summarize: `Summarize ${target}`, quiz: `Make a 5-question quiz on ${target}`, flashcards: `Make 8 flashcards from ${target}` }[mode.id]

  return (
    <div className="mx-auto max-w-xl px-1 py-10 sm:py-16">
      <h1 className="font-serif text-3xl leading-tight font-medium text-balance text-ink sm:text-[2.1rem]">
        {starter.heading}
      </h1>
      <p className="mt-3 leading-relaxed text-ink-soft">
        {!selectedDoc && subject ? (
          <>
            Skimmi will use your {subjectDocs.length === 1 ? 'document' : `${subjectDocs.length} documents`} in{' '}
            <span className="font-medium text-ink break-all">{subject}</span> and cite the pages it used.
          </>
        ) : name ? (
          <>
            Skimmi will use <span className="font-medium text-ink break-all">{name}</span> and cite
            the pages it used.
          </>
        ) : (
          <>Skimmi will search your {documents.length} documents and cite the pages it used.</>
        )}{' '}
        {mode.hint}
      </p>

      <h2 className="mt-8 text-sm font-semibold text-ink">{mode.needsText ? 'Try asking' : 'Try'}</h2>
      <ul className="mt-2 divide-y divide-rule border-y border-rule">
        {suggestions.map((s) => (
          <li key={s || 'default'}>
            <button
              onClick={() => onPick(s)}
              className="w-full py-3 text-left text-[0.95rem] text-ink transition-colors hover:text-navy hover:underline hover:decoration-marker hover:decoration-[5px] hover:underline-offset-[-1px]"
            >
              {label(s)}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
