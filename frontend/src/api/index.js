const BASE = import.meta.env.VITE_API_URL || ''

async function apiFetch(path, options = {}) {
  const { headers, ...rest } = options
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...headers },
    ...rest,
  })
  const data = await res.json()
  return { ok: res.ok, status: res.status, data }
}

export const api = {
  getMerchants: () => apiFetch('/api/v1/merchants/'),

  getMerchant: (id) => apiFetch(`/api/v1/merchants/${id}/`),

  getLedger: (id) => apiFetch(`/api/v1/merchants/${id}/ledger/`),

  getPayouts: (merchantId) =>
    apiFetch(`/api/v1/payouts/list/?merchant_id=${merchantId}`),

  getPayout: (id) => apiFetch(`/api/v1/payouts/${id}/`),

  createPayout: (body, idempotencyKey) =>
    apiFetch('/api/v1/payouts/', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(body),
    }),
}
