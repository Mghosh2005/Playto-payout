# Playto Payout Engine

Cross-border payout infrastructure for Indian merchants. USD in → INR out.

## Quick Start (Docker — recommended)

```bash
git clone <repo>
cd playto-payout
docker-compose up --build
```

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000/api/v1/
- **Django Admin**: http://localhost:8000/admin/

Seed data (3 merchants with credit history) is loaded automatically on first boot.

---

## Local Dev Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- Node 20+

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Create DB
createdb playto

# Run migrations
python manage.py migrate

# Seed merchants
python seed.py

# Start Django
python manage.py runserver

# In a separate terminal — Celery worker
celery -A core worker --loglevel=info

# In a separate terminal — Celery beat (periodic tasks)
celery -A core beat --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

---

## Run Tests

```bash
cd backend
python manage.py test payouts
```

Tests included:
- `ConcurrencyTest` — two simultaneous 6000p requests against 10000p balance; exactly one 201, one 422
- `IdempotencyTest` — same key returns same payout; different keys create separate payouts; key scoped per merchant; missing key rejected; insufficient balance rejected; illegal state transitions blocked

> **Note**: `ConcurrencyTest` uses `TransactionTestCase` (not `TestCase`). This is intentional — `SELECT FOR UPDATE` requires real DB transactions. `TestCase` wraps everything in a savepoint, making `FOR UPDATE` a no-op and letting the race condition through.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/merchants/` | List all merchants |
| GET | `/api/v1/merchants/:id/` | Merchant dashboard (balance + bank accounts) |
| GET | `/api/v1/merchants/:id/ledger/` | Ledger entries (credits + debits) |
| POST | `/api/v1/payouts/` | Create payout *(requires `Idempotency-Key` header)* |
| GET | `/api/v1/payouts/list/?merchant_id=` | Payout history for merchant |
| GET | `/api/v1/payouts/:id/` | Single payout detail |

### POST /api/v1/payouts/

**Headers:**
```
Idempotency-Key: <uuid>   (required)
Content-Type: application/json
```

**Body:**
```json
{
  "merchant_id": "uuid",
  "amount_paise": 50000,
  "bank_account_id": "uuid"
}
```

**Responses:**
- `201` — payout created
- `200` — duplicate idempotency key, returning cached response
- `400` — missing/invalid fields
- `422` — insufficient balance
- `409` — concurrent in-flight conflict (retry)

---

## Architecture

### Money Integrity
Amounts stored as `BigIntegerField` in paise. No `FloatField`. No `DecimalField`. ₹500 = 50000 paise. Integer arithmetic only.

### Ledger Model
Double-entry ledger: credits (customer payments) and debits (payout holds/completions) are separate rows. Balance = `SUM(credits) - SUM(debits)`. Computed at DB level, never in Python. Full history preserved forever.

### Concurrency
`SELECT FOR UPDATE` on the merchant row inside `transaction.atomic()` serializes concurrent payout requests at the PostgreSQL level. Python-level locks don't work across gunicorn workers.

### Idempotency
`unique_together = (merchant, key)` at DB level. Keys expire after 24 hours. First response stored in `response_data` and returned verbatim on replay. In-flight races handled via `IntegrityError` catch.

### State Machine
`pending → processing → completed` or `pending → processing → failed`. All transitions enforced in `Payout.transition_to()`. Terminal states (`completed`, `failed`) have empty allowed-transitions list. Fund returns are atomic with state transitions.

### Retry Logic
Celery beat runs every 30s. Payouts stuck in `processing` > 30s are retried with exponential backoff. Max 3 attempts, then `failed` + funds returned atomically.

---

## Seed Data

| Merchant | Balance |
|----------|---------|
| Arjun Design Studio | ₹245.00 |
| Priya Freelance Writing | ₹157.75 |
| CodeForge Solutions | ₹1,430.00 |
