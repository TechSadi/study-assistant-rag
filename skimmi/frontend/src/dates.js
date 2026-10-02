// "3:41 PM" today, "Yesterday", otherwise "Sep 12".
export function formatWhen(iso) {
  const date = new Date(iso)
  const today = new Date()
  const days = Math.floor((today.setHours(0, 0, 0, 0) - new Date(iso).setHours(0, 0, 0, 0)) / 86400000)
  if (days === 0) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (days === 1) return 'Yesterday'
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}
