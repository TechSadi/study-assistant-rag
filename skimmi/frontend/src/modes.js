import { Layers, Lightbulb, ListChecks, MessageCircleQuestion, ScrollText } from 'lucide-react'

// The study modes, in the order shown. `needsText` modes can't be sent empty;
// the others cover the whole selected document (or pages, or topic) when they are.
export const MODES = [
  {
    id: 'ask',
    label: 'Ask',
    icon: MessageCircleQuestion,
    needsText: true,
    placeholder: 'Ask a question about your documents',
    hint: 'Direct answers with the pages they came from.',
    working: 'Skimmi is writing an answer',
  },
  {
    id: 'explain',
    label: 'Explain',
    icon: Lightbulb,
    needsText: true,
    placeholder: 'What should Skimmi explain?',
    hint: 'Step-by-step explanations in plain language, with examples.',
    working: 'Skimmi is writing an explanation',
  },
  {
    id: 'summarize',
    label: 'Summarize',
    icon: ScrollText,
    placeholder: 'Press Enter to summarize, or add a focus',
    hint: 'A structured summary of the selected document or pages.',
    working: 'Reading and summarizing',
  },
  {
    id: 'quiz',
    label: 'Quiz',
    icon: ListChecks,
    placeholder: 'Press Enter for 5 questions, or describe a quiz',
    hint: 'Multiple-choice questions with instant feedback and a score.',
    working: 'Writing quiz questions',
  },
  {
    id: 'flashcards',
    label: 'Flashcards',
    icon: Layers,
    placeholder: 'Press Enter for 8 cards, or say what to cover',
    hint: 'Question-and-answer cards to flip through for revision.',
    working: 'Making flashcards',
  },
]

export const modeById = (id) => MODES.find((m) => m.id === id) ?? MODES[0]

// Quiz and flashcard messages are stored as JSON. Returns the list under `key`,
// or null if the content isn't that shape (it's then shown as plain markdown).
export function parseStudyContent(content, key) {
  try {
    const list = JSON.parse(content)?.[key]
    return Array.isArray(list) && list.length ? list : null
  } catch {
    return null
  }
}

// What a quiz covered ({doc_id, subject, topic, page_from, page_to}), saved with
// it so "New quiz" can ask for another on the same material. Null for older quizzes.
export function studyRequest(content) {
  try {
    return JSON.parse(content)?.request ?? null
  } catch {
    return null
  }
}
