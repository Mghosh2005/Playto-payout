import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from './api'
import { formatINR, generateUUID, timeAgo } from './utils'
import { StatusBadge } from './components/StatusBadge'

function SkeletonLine({ w = 'w-full', h = 'h-4' }) {
  return <div className={`shimmer rounded ${w} ${h}`} />
}

function BalanceCard({ merchant, loading }) {
  if (loading) {
    return (
      <div className="card grid grid-cols-2 gap-6">
        {[...Array(4)].map((_, i) => <SkeletonLine key={i} h="h-6" />)}
      </div>
    )
  }

  const available = merchant?.available_balance_paise ?? 0
  const held = merchant?.held_balance_paise ?? 0
  const total = available + held

  return (
    <div className="card animate-fade-in">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
        {/* Available */}
        <div className="sm:col-span-1">
          <span className="label">Available Balance</span>
          <div className="font-mono text-3xl font-semibold text-bright mt-1">
            {formatINR(available)}
          </div>
          <div className="text-xs text-dim mt-1 font-mono">{available.toLocaleString()} paise</div>
        </div>

        {/* Held */}
        <div>
          <span className="label">On Hold</span>
          <div className="font-mono text-3xl font-semibold text-hold mt-1">
            {formatINR(held)}
          </div>
          <div className="text-xs text-dim mt-1 font-mono">{held.toLocaleString()} paise</div>
        </div>

        {/* Total received */}
        <div>
          <span className="label">Total Received</span>
          <div className="font-mono text-3xl font-semibold text-muted mt-1">
            {formatINR(total)}
          </div>
          <div className="text-xs text-dim mt-1 font-mono">{total.toLocaleString()} paise</div>
        </div>
      </div>

      {/* Balance bar */}
      {total > 0 && (
        <div className="mt-5">
          <div className="h-1.5 bg-surface rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all duration-700"
              style={{ width: `${Math.round((available / total) * 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-muted font-mono mt-1">
            <span>{Math.round((available / total) * 100)}% available</span>
            <span>{Math.round((held / total) * 100)}% held</span>
          </div>
        </div>
      )}
    </div>
  )
}

function PayoutForm({ merchant, onSuccess }) {
  const [amountRupees, setAmountRupees] = useState('')
  const [bankId, setBankId] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const idempotencyKey = useRef(generateUUID())

  useEffect(() => {
    if (merchant?.bank_accounts?.length) {
      setBankId(merchant.bank_accounts[0].id)
    }
  }, [merchant])

  const amountPaise = amountRupees ? Math.round(parseFloat(amountRupees) * 100) : 0

  const submit = async () => {
    if (!amountPaise || amountPaise <= 0) {
      return setResult({ error: 'Enter a valid amount' })
    }
    if (!bankId) return setResult({ error: 'Select a bank account' })

    setLoading(true)
    setResult(null)

    const { ok, status, data } = await api.createPayout(
      {
        merchant_id: merchant.id,
        amount_paise: amountPaise,
        bank_account_id: bankId,
      },
      idempotencyKey.current
    )

    setLoading(false)

    if (ok) {
      setResult({ success: true, data })
      setAmountRupees('')
      idempotencyKey.current = generateUUID()
      onSuccess?.()
    } else {
      setResult({ error: data?.error || `Error ${status}`, detail: data })
    }
  }

  return (
    <div className="card animate-slide-up">
      <div className="flex items-center justify-between mb-5">
        <h3 className="font-medium text-bright">Request Payout</h3>
        <span
          title="Idempotency key (auto-rotates after submit)"
          className="text-xs font-mono text-muted bg-surface border border-border px-2 py-1 rounded cursor-help"
        >
          key: {idempotencyKey.current.slice(0, 8)}…
        </span>
      </div>

      <div className="space-y-4">
        <div>
          <label className="label">Amount (₹)</label>
          <input
            className="input"
            type="number"
            step="0.01"
            min="1"
            placeholder="e.g. 500.00"
            value={amountRupees}
            onChange={e => setAmountRupees(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
          />
          {amountPaise > 0 && (
            <div className="text-xs text-dim mt-1 font-mono">
              = {amountPaise.toLocaleString()} paise (stored as integer)
            </div>
          )}
        </div>

        <div>
          <label className="label">Bank Account</label>
          <select
            className="input"
            value={bankId}
            onChange={e => setBankId(e.target.value)}
          >
            {merchant?.bank_accounts?.map(b => (
              <option key={b.id} value={b.id}>
                {b.account_holder_name} — ···{b.account_number.slice(-4)} · {b.ifsc_code}
              </option>
            ))}
          </select>
        </div>

        <button
          className="btn-primary w-full"
          onClick={submit}
          disabled={loading || !amountPaise || !bankId}
        >
          {loading
            ? <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                Submitting…
              </span>
            : 'Submit Payout'
          }
        </button>
      </div>

      {result && (
        <div className={`mt-4 p-3 rounded-lg text-sm font-mono border animate-fade-in ${
          result.success
            ? 'bg-success/10 border-success/30 text-success'
            : 'bg-danger/10 border-danger/30 text-danger'
        }`}>
          {result.success
            ? `✓ Payout created — ID ${result.data.id?.slice(0, 8)}…`
            : `✗ ${result.error}`
          }
          {result.detail?.available_paise != null && (
            <div className="text-xs mt-1.5 text-dim space-y-0.5">
              <div>Available: {formatINR(result.detail.available_paise)}</div>
              <div>Requested: {formatINR(result.detail.requested_paise)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function LedgerTable({ merchantId, refresh }) {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!merchantId) return
    const { ok, data } = await api.getLedger(merchantId)
    if (ok) setEntries(data)
    setLoading(false)
  }, [merchantId])

  useEffect(() => { load() }, [load, refresh])

  return (
    <div className="card animate-fade-in">
      <h3 className="font-medium text-bright mb-4">Ledger</h3>

      {loading ? (
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => <SkeletonLine key={i} h="h-5" />)}
        </div>
      ) : entries.length === 0 ? (
        <p className="text-dim text-sm py-6 text-center">No ledger entries</p>
      ) : (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Type', 'Amount', 'Description', 'When'].map(h => (
                  <th key={h} className={`py-2 pr-4 text-dim font-normal text-xs uppercase tracking-wider ${
                    h === 'Amount' || h === 'When' ? 'text-right' : 'text-left'
                  } ${h === 'Description' ? 'hidden sm:table-cell' : ''}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr
                  key={e.id}
                  className="border-b border-border/40 hover:bg-surface/50 transition-colors"
                  style={{ animationDelay: `${i * 30}ms` }}
                >
                  <td className="py-2.5 pr-4">
                    <span className={`font-mono text-xs font-semibold ${
                      e.entry_type === 'credit' ? 'text-success' : 'text-danger'
                    }`}>
                      {e.entry_type === 'credit' ? 'CR' : 'DR'}
                    </span>
                  </td>
                  <td className={`py-2.5 pr-4 font-mono text-sm text-right font-medium ${
                    e.entry_type === 'credit' ? 'text-success' : 'text-danger'
                  }`}>
                    {e.entry_type === 'credit' ? '+' : '−'}{formatINR(e.amount_paise)}
                  </td>
                  <td className="py-2.5 pr-4 text-dim text-xs hidden sm:table-cell max-w-xs truncate">
                    {e.description}
                  </td>
                  <td className="py-2.5 text-right font-mono text-xs text-muted whitespace-nowrap">
                    {timeAgo(e.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function PayoutHistory({ merchantId, refresh }) {
  const [payouts, setPayouts] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!merchantId) return
    const { ok, data } = await api.getPayouts(merchantId)
    if (ok) setPayouts(data)
    setLoading(false)
  }, [merchantId])

  // Live polling — 3s interval
  useEffect(() => {
    load()
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [load, refresh])

  const hasLive = payouts.some(p => p.status === 'pending' || p.status === 'processing')

  return (
    <div className="card animate-fade-in">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-medium text-bright">Payout History</h3>
        <div className="flex items-center gap-3">
          {hasLive && (
            <span className="flex items-center gap-1.5 text-xs text-accent font-mono">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-dot" />
              Live
            </span>
          )}
          <span className="text-xs text-muted font-mono">{payouts.length} total</span>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <SkeletonLine key={i} h="h-10" />)}
        </div>
      ) : payouts.length === 0 ? (
        <p className="text-dim text-sm py-6 text-center">No payouts yet — submit one above</p>
      ) : (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 pr-4 text-dim font-normal text-xs uppercase tracking-wider">ID</th>
                <th className="text-right py-2 pr-4 text-dim font-normal text-xs uppercase tracking-wider">Amount</th>
                <th className="text-center py-2 pr-4 text-dim font-normal text-xs uppercase tracking-wider">Status</th>
                <th className="text-center py-2 pr-4 text-dim font-normal text-xs uppercase tracking-wider hidden sm:table-cell">Attempts</th>
                <th className="text-right py-2 text-dim font-normal text-xs uppercase tracking-wider">When</th>
              </tr>
            </thead>
            <tbody>
              {payouts.map((p, i) => (
                <tr
                  key={p.id}
                  className="border-b border-border/40 hover:bg-surface/50 transition-colors"
                >
                  <td className="py-3 pr-4 font-mono text-xs text-muted">
                    {p.id.slice(0, 8)}…
                  </td>
                  <td className="py-3 pr-4 font-mono text-right text-bright font-medium">
                    {formatINR(p.amount_paise)}
                  </td>
                  <td className="py-3 pr-4 text-center">
                    <StatusBadge status={p.status} />
                  </td>
                  <td className="py-3 pr-4 text-center font-mono text-xs text-dim hidden sm:table-cell">
                    {p.attempts} / {p.max_attempts ?? 3}
                  </td>
                  <td className="py-3 text-right font-mono text-xs text-muted whitespace-nowrap">
                    {timeAgo(p.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [merchants, setMerchants] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [merchant, setMerchant] = useState(null)
  const [loadingMerchant, setLoadingMerchant] = useState(false)
  const [refresh, setRefresh] = useState(0)

  // Load merchant list once
  useEffect(() => {
    api.getMerchants().then(({ ok, data }) => {
      if (ok && data.length) {
        setMerchants(data)
        setSelectedId(data[0].id)
      }
    })
  }, [])

  // Load selected merchant detail
  useEffect(() => {
    if (!selectedId) return
    setLoadingMerchant(true)
    api.getMerchant(selectedId).then(({ ok, data }) => {
      if (ok) setMerchant(data)
      setLoadingMerchant(false)
    })
  }, [selectedId, refresh])

  // Auto-refresh balance every 4s
  useEffect(() => {
    const timer = setInterval(() => setRefresh(r => r + 1), 4000)
    return () => clearInterval(timer)
  }, [])

  const handlePayoutSuccess = () => setRefresh(r => r + 1)

  return (
    <div className="min-h-screen bg-ink text-bright">

      {/* Top nav */}
      <header className="border-b border-border bg-surface/70 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 shrink-0">
            {/* Logo */}
            <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center shrink-0">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M7 1L13 4.5V9.5L7 13L1 9.5V4.5L7 1Z" stroke="white" strokeWidth="1.5" strokeLinejoin="round"/>
                <path d="M7 1V13" stroke="white" strokeWidth="1" strokeOpacity="0.5"/>
                <path d="M1 4.5L13 9.5" stroke="white" strokeWidth="1" strokeOpacity="0.5"/>
                <path d="M13 4.5L1 9.5" stroke="white" strokeWidth="1" strokeOpacity="0.5"/>
              </svg>
            </div>
            <span className="font-semibold text-bright">Playto Pay</span>
            <span className="text-border text-lg hidden sm:block">·</span>
            <span className="text-dim text-sm font-mono hidden sm:block">payout engine</span>
          </div>

          {/* Merchant switcher */}
          <div className="flex items-center gap-2">
            <span className="text-dim text-xs hidden sm:block">Viewing as</span>
            <select
              className="bg-panel border border-border rounded-lg px-3 py-1.5 text-sm text-bright
                         focus:outline-none focus:border-accent transition-colors font-mono max-w-[180px]"
              value={selectedId || ''}
              onChange={e => {
                setSelectedId(e.target.value)
                setMerchant(null)
              }}
            >
              {merchants.map(m => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          </div>
        </div>
      </header>

      {/* Page */}
      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8 space-y-6">

        {/* Balance overview */}
        <BalanceCard merchant={merchant} loading={loadingMerchant} />

        {/* Two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* Left: payout form */}
          <div className="lg:col-span-1">
            <PayoutForm merchant={merchant} onSuccess={handlePayoutSuccess} />
          </div>

          {/* Right: history + ledger */}
          <div className="lg:col-span-2 space-y-6">
            <PayoutHistory merchantId={selectedId} refresh={refresh} />
            <LedgerTable merchantId={selectedId} refresh={refresh} />
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-border mt-16 py-5">
        <div className="max-w-6xl mx-auto px-6 flex items-center justify-between">
          <span className="text-xs text-muted font-mono">
            Playto Payout Engine — Founding Engineer Challenge 2026
          </span>
          <span className="flex items-center gap-1.5 text-xs text-success font-mono">
            <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse-dot" />
            All systems operational
          </span>
        </div>
      </footer>

    </div>
  )
}
