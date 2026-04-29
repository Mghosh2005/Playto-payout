import { STATUS_META } from '../utils'

export function StatusBadge({ status }) {
  const meta = STATUS_META[status] || { label: status, color: 'text-dim', dot: 'bg-dim', pulse: false }
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-mono font-medium ${meta.color}`}>
      <span
        className={`w-1.5 h-1.5 rounded-full ${meta.dot} ${meta.pulse ? 'animate-pulse-dot' : ''}`}
      />
      {meta.label}
    </span>
  )
}
