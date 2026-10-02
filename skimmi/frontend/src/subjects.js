// Documents are organized by subject (e.g. "Biology") and an optional course
// (e.g. "BIO 101"). A document without a subject has subject ''.

const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true })

export function subjectsOf(documents) {
  return [...new Set(documents.map((d) => d.subject).filter(Boolean))].sort(byName)
}

export function coursesOf(documents, subject) {
  return [...new Set(documents.filter((d) => d.subject === subject).map((d) => d.course).filter(Boolean))].sort(byName)
}

// [{ subject, docs }] in name order, with the documents that have no subject
// last. Within a subject, documents are ordered by course, then name.
export function groupBySubject(documents) {
  const groups = new Map()
  for (const doc of documents) {
    if (!groups.has(doc.subject)) groups.set(doc.subject, [])
    groups.get(doc.subject).push(doc)
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (!a ? 1 : !b ? -1 : byName(a, b)))
    .map(([subject, docs]) => ({
      subject,
      docs: docs.sort((x, y) => byName(x.course, y.course) || byName(x.filename, y.filename)),
    }))
}
