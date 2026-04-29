/** Format paise (integer) to Indian rupee string: ₹1,234.56 */
export function formatINR(paise) {
  if (paise == null) return '—'
  const rupees = paise / 100
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
  }).format(rupees)
}

/** Generate a UUID v4 for idempotency keys */
export function generateUUID() {
  return crypto.randomUUID()
}

/** Relative time: "2 min ago", "just now" */
export function timeAgo(dateStr) {
  const diff = Date.now() - new Date(dateStr).getTime()
  const secs = Math.floor(diff / 1000)
  if (secs < 10) return 'just now'
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return new Date(dateStr).toLocaleDateString('en-IN')
}

export const STATUS_META = {
  pending: { label: 'Pending', color: 'text-warning', dot: 'bg-warning', pulse: true },
  processing: { label: 'Processing', color: 'text-accent', dot: 'bg-accent', pulse: true },
  completed: { label: 'Completed', color: 'text-success', dot: 'bg-success', pulse: false },
  failed: { label: 'Failed', color: 'text-danger', dot: 'bg-danger', pulse: false },
}
