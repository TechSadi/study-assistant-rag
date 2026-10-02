import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, CircleCheck, CircleX, Eye, EyeOff, Info, ListChecks, Loader2, RotateCcw, X } from 'lucide-react'
import { AssistantFrame, RichText } from './Messages'
import Sources from './Sources'

function useRevealed(count) {
  const [revealed, setRevealed] = useState(() => new Set())
  const toggle = (i) =>
    setRevealed((set) => {
      const next = new Set(set)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  const allShown = revealed.size === count
  const toggleAll = () => setRevealed(allShown ? new Set() : new Set(Array.from({ length: count }, (_, i) => i)))
  return { revealed, toggle, allShown, toggleAll }
}

function StudyHeader({ title, detail, allShown, onToggleAll, showLabel, hideLabel }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h3 className="text-base font-semibold text-ink">
        {title} <span className="font-normal text-ink-soft">· {detail}</span>
      </h3>
      <button
        onClick={onToggleAll}
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-navy underline-offset-2 hover:underline"
      >
        {allShown ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
        {allShown ? hideLabel : showLabel}
      </button>
    </div>
  )
}

// Where an answer is found. `missing` is shown when the question has no usable page.
function PageCite({ item, missing }) {
  if (!item.page) return missing ? <span className="text-xs text-ink-soft">{missing}</span> : null
  return (
    <span className="text-xs text-ink-soft">
      <span className="marker px-0.5 font-medium text-ink">{item.source}</span>, page {item.page}
    </span>
  )
}

// --- Quiz -------------------------------------------------------------------

// Only multiple-choice questions are scored. Quizzes saved before scoring
// existed can also have short-answer questions, which are just revealed.
const isChoice = (q) => q.type === 'multiple_choice' && Array.isArray(q.options) && q.options.length > 1
const isRight = (q, answer) => answer != null && q.options[answer] === q.answer
const letter = (i) => String.fromCharCode(65 + i)

function verdict(percent) {
  if (percent >= 80) return 'Nicely done.'
  if (percent >= 50) return 'Good effort. Review the questions you missed.'
  return 'Worth another look. Revisit the cited pages, then try again.'
}

// One question at a time: choose an option, check it, read why, move on. At the
// end the score is shown and saved to the student's progress.
export function QuizMessage({ questions, sources, messageId, lastAttempt, request, onSaveAttempt, onNewQuiz, busy }) {
  const blank = () => questions.map(() => null)
  const [answers, setAnswers] = useState(blank)
  const [checked, setChecked] = useState(() => questions.map(() => false))
  const [index, setIndex] = useState(0)
  const [finished, setFinished] = useState(false)
  const [save, setSave] = useState({ status: 'idle' }) // 'idle' | 'saving' | 'saved' | 'error'
  const [lastScore, setLastScore] = useState(lastAttempt ?? null)
  const focusTarget = useRef(null)
  const moved = useRef(false)

  const scored = questions.filter(isChoice).length
  const correct = questions.filter((q, i) => isChoice(q) && isRight(q, answers[i])).length

  // After moving to the next question or the results, put focus on its heading
  // so keyboard and screen reader users continue from there.
  useEffect(() => {
    if (moved.current) focusTarget.current?.focus({ preventScroll: false })
  }, [index, finished])

  async function saveResult() {
    if (!scored || messageId == null) return
    setSave({ status: 'saving' })
    try {
      const attempt = await onSaveAttempt(messageId, answers)
      setSave({ status: 'saved' })
      setLastScore(attempt)
    } catch (e) {
      setSave({ status: 'error', error: e.message })
    }
  }

  function check() {
    setChecked((list) => list.map((c, i) => (i === index ? true : c)))
  }

  function next() {
    moved.current = true
    if (index + 1 < questions.length) {
      setIndex(index + 1)
    } else {
      setFinished(true)
      saveResult()
    }
  }

  function retry() {
    moved.current = true
    setAnswers(blank())
    setChecked(questions.map(() => false))
    setIndex(0)
    setFinished(false)
    setSave({ status: 'idle' })
  }

  return (
    <AssistantFrame>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-base font-semibold text-ink">
          Practice quiz{' '}
          <span className="font-normal text-ink-soft">
            · {questions.length} {questions.length === 1 ? 'question' : 'questions'}
          </span>
        </h3>
        {lastScore && (
          <span className="text-xs text-ink-soft">
            Last score{' '}
            <span className="font-semibold text-ink">
              {lastScore.correct}/{lastScore.total}
            </span>
          </span>
        )}
      </div>

      <ol className="mt-3 flex gap-1" aria-hidden="true">
        {questions.map((q, i) => {
          let tone = 'bg-rule'
          if (checked[i] && isChoice(q)) tone = isRight(q, answers[i]) ? 'bg-ok' : 'bg-danger'
          else if (checked[i]) tone = 'bg-ink-faint'
          else if (i === index && !finished) tone = 'bg-navy'
          return <li key={i} className={`h-1.5 flex-1 rounded-full transition-colors ${tone}`} />
        })}
      </ol>

      {finished ? (
        <QuizResults
          questions={questions}
          answers={answers}
          correct={correct}
          scored={scored}
          save={save}
          onRetrySave={saveResult}
          onRetry={retry}
          onNewQuiz={() => onNewQuiz(request)}
          busy={busy}
          headingRef={focusTarget}
        />
      ) : (
        <QuizQuestion
          key={index}
          question={questions[index]}
          number={index + 1}
          count={questions.length}
          name={`quiz-${messageId}-${index}`}
          answer={answers[index]}
          checked={checked[index]}
          last={index + 1 === questions.length}
          onChoose={(choice) => setAnswers((list) => list.map((a, i) => (i === index ? choice : a)))}
          onCheck={check}
          onNext={next}
          headingRef={focusTarget}
        />
      )}

      <Sources sources={sources} />
    </AssistantFrame>
  )
}

function QuizQuestion({ question: q, number, count, name, answer, checked, last, onChoose, onCheck, onNext, headingRef }) {
  const choice = isChoice(q)
  const right = choice && isRight(q, answer)
  const correctIndex = choice ? q.options.indexOf(q.answer) : -1

  return (
    <div className="mt-4 rounded-xl border border-rule bg-sheet p-4 sm:p-5">
      <p
        ref={headingRef}
        tabIndex={-1}
        className="text-xs font-semibold tracking-wide text-ink-soft uppercase focus-visible:outline-none"
      >
        Question {number} of {count}
        {!choice && ' · Short answer'}
      </p>

      <fieldset className="mt-1.5">
        <legend className="w-full">
          <RichText className="text-[1.05rem]">{q.question}</RichText>
        </legend>

        {choice && (
          <div className="mt-3 space-y-1.5">
            {q.options.map((option, i) => {
              const chosen = answer === i
              const isAnswer = i === correctIndex
              let tone = chosen
                ? 'border-navy bg-paper shadow-[inset_0_0_0_1px_var(--color-navy)]'
                : 'border-rule hover:border-rule-strong hover:bg-paper/60'
              let badge = chosen ? 'bg-navy text-white' : 'bg-paper text-ink-soft'
              if (checked) {
                tone = isAnswer
                  ? 'border-ok/40 bg-ok-soft'
                  : chosen
                    ? 'border-danger/40 bg-danger-soft'
                    : 'border-rule text-ink-soft'
                badge = isAnswer ? 'bg-ok text-white' : chosen ? 'bg-danger text-white' : 'bg-paper text-ink-faint'
              }
              return (
                <label
                  key={i}
                  className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-[0.95rem] transition-colors has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-navy ${tone} ${
                    checked ? 'cursor-default' : 'cursor-pointer'
                  }`}
                >
                  <input
                    type="radio"
                    name={name}
                    className="sr-only"
                    checked={chosen}
                    disabled={checked}
                    onChange={() => onChoose(i)}
                  />
                  <span
                    className={`grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold ${badge}`}
                    aria-hidden="true"
                  >
                    {checked && isAnswer ? (
                      <Check className="size-3.5" />
                    ) : checked && chosen ? (
                      <X className="size-3.5" />
                    ) : (
                      letter(i)
                    )}
                  </span>
                  <RichText className="min-w-0 flex-1 pt-px font-sans text-[0.95rem]">{option}</RichText>
                  {checked && isAnswer && <span className="sr-only">(correct answer)</span>}
                  {checked && chosen && !isAnswer && <span className="sr-only">(your answer, incorrect)</span>}
                </label>
              )
            })}
          </div>
        )}
      </fieldset>

      <div aria-live="polite">
        {checked && (
          <div
            className={`mt-4 rounded-lg border-l-[3px] px-3 py-2.5 ${
              !choice ? 'border-marker bg-paper' : right ? 'border-ok bg-ok-soft/60' : 'border-danger bg-danger-soft/60'
            }`}
          >
            {choice ? (
              <p className={`flex items-center gap-1.5 text-sm font-semibold ${right ? 'text-ok' : 'text-danger'}`}>
                {right ? (
                  <CircleCheck className="size-4" aria-hidden="true" />
                ) : (
                  <CircleX className="size-4" aria-hidden="true" />
                )}
                {right ? 'Correct' : `Not quite. The answer is ${letter(correctIndex)}.`}
              </p>
            ) : (
              <>
                <p className="text-xs font-semibold text-ink-soft">Model answer</p>
                <RichText className="text-[0.95rem]">{q.answer}</RichText>
              </>
            )}
            {q.explanation && <RichText className="mt-1 font-sans text-sm text-ink">{q.explanation}</RichText>}
            <p className="mt-1.5">
              <PageCite item={q} missing="No page reference for this question. Check the sources below." />
            </p>
          </div>
        )}
      </div>

      <div className="mt-4 flex justify-end">
        {!checked ? (
          <button
            onClick={onCheck}
            disabled={choice && answer == null}
            className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-navy px-4 text-sm font-semibold text-white transition-colors hover:bg-navy-deep disabled:bg-rule disabled:text-ink-faint"
          >
            {choice ? 'Check answer' : 'Show model answer'}
          </button>
        ) : (
          <button
            onClick={onNext}
            className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-navy px-4 text-sm font-semibold text-white transition-colors hover:bg-navy-deep"
          >
            {last ? 'See results' : 'Next question'}
            <ArrowRight className="size-4" aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  )
}

function QuizResults({ questions, answers, correct, scored, save, onRetrySave, onRetry, onNewQuiz, busy, headingRef }) {
  const percent = scored ? Math.round((correct / scored) * 100) : 0

  return (
    <div className="mt-4 rounded-xl border border-rule bg-sheet p-4 sm:p-5">
      <p
        ref={headingRef}
        tabIndex={-1}
        className="text-xs font-semibold tracking-wide text-ink-soft uppercase focus-visible:outline-none"
      >
        Quiz complete
      </p>
      {scored ? (
        <>
          <p className="mt-1 font-serif text-4xl leading-tight font-medium text-ink">
            {correct}
            <span className="text-2xl text-ink-soft"> of {scored} correct</span>
          </p>
          <p className="mt-1 text-sm text-ink">
            <span className="font-semibold">{percent}%</span> · {verdict(percent)}
          </p>
        </>
      ) : (
        <p className="mt-1 text-sm text-ink">This quiz has no multiple-choice questions, so it isn't scored.</p>
      )}

      <p className="mt-2 min-h-5 text-xs" aria-live="polite">
        {save.status === 'saving' && (
          <span className="inline-flex items-center gap-1.5 text-ink-soft">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> Saving to your progress…
          </span>
        )}
        {save.status === 'saved' && (
          <span className="inline-flex items-center gap-1.5 text-ok">
            <Check className="size-3.5" aria-hidden="true" /> Saved to your progress
          </span>
        )}
        {save.status === 'error' && (
          <span className="text-danger">
            Couldn't save this result. {save.error}{' '}
            <button onClick={onRetrySave} className="font-semibold underline underline-offset-2">
              Try again
            </button>
          </span>
        )}
      </p>

      <h4 className="mt-4 text-sm font-semibold text-ink">Review</h4>
      <ol className="mt-1 divide-y divide-rule border-y border-rule">
        {questions.map((q, i) => {
          const choice = isChoice(q)
          const right = choice && isRight(q, answers[i])
          return (
            <li key={i} className="flex gap-3 py-3">
              {!choice ? (
                <Info className="mt-0.5 size-4.5 shrink-0 text-ink-faint" aria-label="Not scored" />
              ) : right ? (
                <CircleCheck className="mt-0.5 size-4.5 shrink-0 text-ok" aria-label="Correct" />
              ) : (
                <CircleX className="mt-0.5 size-4.5 shrink-0 text-danger" aria-label="Incorrect" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex gap-1.5 text-[0.95rem] font-medium text-ink">
                  <span className="shrink-0">{i + 1}.</span>
                  <RichText className="min-w-0 flex-1 font-sans text-[0.95rem]">{q.question}</RichText>
                </div>
                {choice && !right && (
                  <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-sm">
                    <div className="contents">
                      <dt className="text-ink-soft">You chose</dt>
                      <dd className="min-w-0 text-ink">
                        <RichText className="font-sans text-sm">{`${letter(answers[i])}. ${q.options[answers[i]]}`}</RichText>
                      </dd>
                    </div>
                    <div className="contents">
                      <dt className="text-ink-soft">Answer</dt>
                      <dd className="min-w-0 font-medium text-ink">
                        <RichText className="font-sans text-sm">{`${letter(q.options.indexOf(q.answer))}. ${q.answer}`}</RichText>
                      </dd>
                    </div>
                  </dl>
                )}
                <p className="mt-1">
                  <PageCite item={q} />
                </p>
              </div>
            </li>
          )
        })}
      </ol>

      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button
          onClick={onRetry}
          className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-rule-strong bg-sheet px-4 text-sm font-semibold text-ink transition-colors hover:border-navy"
        >
          <RotateCcw className="size-4" aria-hidden="true" /> Retry quiz
        </button>
        <button
          onClick={onNewQuiz}
          disabled={busy}
          title="Another quiz on the same material, with different questions"
          className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-navy px-4 text-sm font-semibold text-white transition-colors hover:bg-navy-deep disabled:bg-rule disabled:text-ink-faint"
        >
          <ListChecks className="size-4" aria-hidden="true" /> New quiz
        </button>
      </div>
    </div>
  )
}

// --- Flashcards -------------------------------------------------------------

export function FlashcardsMessage({ cards, sources }) {
  const { revealed, toggle, allShown, toggleAll } = useRevealed(cards.length)

  return (
    <AssistantFrame>
      <StudyHeader
        title="Flashcards"
        detail={`${revealed.size} of ${cards.length} revealed`}
        allShown={allShown}
        onToggleAll={toggleAll}
        showLabel="Show all"
        hideLabel="Hide all"
      />
      <p className="mt-1 text-sm text-ink-soft">Answer each card in your head, then tap it to check.</p>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {cards.map((card, i) => {
          const shown = revealed.has(i)
          return (
            <li key={i}>
              <button
                onClick={() => toggle(i)}
                aria-pressed={shown}
                className={`flex h-full min-h-36 w-full flex-col rounded-xl border p-4 text-left transition-colors ${
                  shown ? 'border-ink/15 bg-marker-soft/70' : 'border-rule bg-sheet hover:border-navy'
                }`}
              >
                <span className="text-xs font-semibold text-ink-soft">
                  Card {i + 1} · {shown ? 'Answer' : 'Question'}
                </span>
                <RichText className={`mt-1.5 flex-1 ${shown ? 'text-[0.95rem]' : 'text-[1.05rem] font-medium'}`}>
                  {shown ? card.back : card.front}
                </RichText>
                <span className="mt-3 flex items-center justify-between gap-2 text-xs text-ink-soft">
                  {shown ? <PageCite item={card} /> : <span>Tap to reveal</span>}
                  {shown && <span className="shrink-0">Tap to hide</span>}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      <Sources sources={sources} />
    </AssistantFrame>
  )
}
