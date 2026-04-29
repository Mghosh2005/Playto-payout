# EXPLAINER.md — Playto Payout Engine

---

## 1. The Ledger

### Balance Calculation Query

```python
# merchants/models.py — Merchant.get_balance_paise()
result = LedgerEntry.objects.filter(merchant=self).aggregate(
    balance=Sum(
        'amount_paise',
        filter=Q(entry_type=LedgerEntry.CREDIT)
    ) - Sum(
        'amount_paise',
        filter=Q(entry_type=LedgerEntry.DEBIT)
    )
)
```

In the payout creation view, the balance check uses a tighter version inside a locked transaction:

```python
# payouts/views.py — inside SELECT FOR UPDATE block
agg = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
    credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT)),
    debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT))
)
available_balance = (agg['credits'] or 0) - (agg['debits'] or 0)
```

### Why this model?

Credits and debits are separate rows in the `ledger_entries` table rather than a single mutable balance column. This is the double-entry bookkeeping pattern — it means the full history is preserved forever and the balance is always derivable from first principles. You can never have a situation where a bug silently corrupts a balance field. Every rupee is accounted for by a specific ledger row with a description and timestamp.

Amounts are stored as `BigIntegerField` in paise (1 rupee = 100 paise). This eliminates floating point entirely. ₹500 is stored as `50000`. All arithmetic is integer arithmetic — no rounding errors, no IEEE 754 surprises.

---

## 2. The Lock

### Exact code that prevents concurrent overdraw

```python
# payouts/views.py
with transaction.atomic():
    # STEP 1: Lock the merchant row
    locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)

    # STEP 2: Compute balance at DB level
    agg = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
        credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT)),
        debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT))
    )
    available_balance = (agg['credits'] or 0) - (agg['debits'] or 0)

    if available_balance < amount_paise:
        return Response({'error': 'Insufficient balance'}, status=422)

    # STEP 3: Create payout + debit ledger entry atomically
    payout = Payout.objects.create(...)
    LedgerEntry.objects.create(entry_type=LedgerEntry.DEBIT, ...)
```

### The database primitive

`SELECT FOR UPDATE` on the merchant row. When two concurrent gunicorn workers both try to process a payout for the same merchant, the first one to reach `select_for_update()` acquires a row-level exclusive lock on that merchant's DB row. The second worker blocks at that line and waits. It cannot proceed until the first transaction commits or rolls back. By the time the second worker gets the lock, the first worker has already written the debit entry — so when the second worker recomputes the balance, it sees the already-reduced amount and rejects with 422.

Python-level locks (threading.Lock, etc.) do not work here because gunicorn runs multiple worker processes, not threads in the same process. A Python lock only works within a single process. PostgreSQL row-level locking works across all connections regardless of process boundaries.

---

## 3. The Idempotency

### How the system knows it has seen a key before

```python
# payouts/models.py
class IdempotencyKey(models.Model):
    merchant = models.ForeignKey('merchants.Merchant', ...)
    key = models.CharField(max_length=255)
    response_data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('merchant', 'key')]  # DB-level enforcement
```

On every POST to `/api/v1/payouts/`, we look up `(merchant_id, idempotency_key)` in the `idempotency_keys` table with a 24-hour TTL filter:

```python
existing_key = IdempotencyKey.objects.filter(
    merchant=merchant,
    key=idempotency_key,
    created_at__gte=ttl_cutoff  # 24-hour expiry
).first()

if existing_key is not None:
    if existing_key.response_data:
        return Response(existing_key.response_data, status=HTTP_200_OK)
```

The first successful response is serialized and stored in `response_data` (a JSONField). Subsequent calls with the same key return this stored blob verbatim — the exact same response, same status code.

### What happens if the first request is still in flight when the second arrives?

The `unique_together = [('merchant', 'key')]` constraint is enforced at the database level. If two concurrent requests both pass the initial lookup check (both find no existing key) and race to insert, only one will succeed — the other gets a `psycopg2.IntegrityError`. We catch this:

```python
idem_key, created = IdempotencyKey.objects.get_or_create(
    merchant=locked_merchant,
    key=idempotency_key,
    defaults={'payout': payout}
)

if not created:
    raise IntegrityError("Concurrent idempotency key conflict")
```

The `IntegrityError` causes the `transaction.atomic()` block to roll back (undoing the payout creation and debit entry), and we return HTTP 409 to tell the caller to retry. This is correct behaviour — the caller should retry and will then hit the normal idempotency path.

---

## 4. The State Machine

### Where failed-to-completed is blocked

```python
# payouts/models.py — Payout.LEGAL_TRANSITIONS
LEGAL_TRANSITIONS = {
    'pending':    ['processing'],
    'processing': ['completed', 'failed'],
    'completed':  [],   # terminal — no transitions allowed
    'failed':     [],   # terminal — no transitions allowed
}

def transition_to(self, new_status):
    allowed = self.LEGAL_TRANSITIONS.get(self.status, [])
    if new_status not in allowed:
        raise ValueError(
            f"Illegal state transition: {self.status} → {new_status}. "
            f"Allowed from {self.status}: {allowed}"
        )
    self.status = new_status
    if new_status == self.PROCESSING:
        self.processing_started_at = timezone.now()
```

`transition_to()` is the **only** place in the codebase where `payout.status` changes. Both `tasks.py` and `views.py` call it exclusively — there is no `payout.status = 'completed'` anywhere. If anything tries `failed → completed`, `LEGAL_TRANSITIONS['failed']` is an empty list, so `new_status not in []` is always True, and a `ValueError` is raised. The caller catches this and logs it.

Fund returns on failure are atomic with the state transition — both happen inside the same `transaction.atomic()` block:

```python
with transaction.atomic():
    payout.transition_to(Payout.FAILED)
    payout.save(...)
    LedgerEntry.objects.create(
        entry_type=LedgerEntry.CREDIT,
        amount_paise=payout.amount_paise,
        description=f'Funds returned for failed payout {payout.id}',
        ...
    )
```

If either the state save or the ledger credit fails, both roll back. The merchant never loses funds.

---

## 5. The AI Audit

### Where AI gave subtly wrong code

When implementing the idempotency key storage, the AI initially generated this pattern:

```python
# What AI gave — WRONG
idem_key.response_data = response.data
idem_key.save(update_fields=['response_data'])
```

There were two problems here:

**Problem 1:** `response` was not defined in scope — the local variable holding the serialized data was named `response_data`, not `response`. The AI confused the DRF `Response` object (which hadn't been constructed yet at that point in the code) with the serialized dict. This would have caused a `NameError` at runtime.

**Problem 2:** Even if `response` had been defined, `response.data` is a DRF `ReturnDict` which contains UUID objects (from the `id` fields). PostgreSQL's JSONField cannot serialize raw UUID objects — it would throw `TypeError: Object of type UUID is not JSON serializable` when psycopg2 tried to store it.

**What I replaced it with:**

```python
# Correct version
response_data = serializer.data
idem_key.response_data = json.loads(json.dumps(dict(response_data), default=str))
idem_key.save(update_fields=['response_data'])
```

`json.dumps(..., default=str)` converts all non-serializable objects (UUIDs, Decimals) to strings. `json.loads()` then turns it back into a plain Python dict that PostgreSQL can store cleanly. This pattern also required adding `import json` at the top of `views.py`, which the AI had omitted.

The bug was caught by reading the actual error in the Django logs during testing — `NameError: name 'response' is not defined` on the first attempt, then `TypeError: Object of type UUID is not JSON serializable` after fixing the variable name.

---

## Architecture Summary

| Concern | Approach |
|---|---|
| Money storage | `BigIntegerField` in paise — no floats ever |
| Balance calculation | DB-level `SUM()` aggregation, never Python arithmetic on fetched rows |
| Concurrency | `SELECT FOR UPDATE` on merchant row inside `transaction.atomic()` |
| Idempotency | `unique_together` DB constraint + stored response blob + 24hr TTL |
| State machine | `transition_to()` enforces `LEGAL_TRANSITIONS` dict — only one place status changes |
| Fund safety | Failed payout credit happens atomically with state transition |
| Retry | Celery beat every 30s, exponential backoff (`2^attempts`), max 3 attempts |
| Background simulation | 70% success / 20% failure / 10% hang via `random.random()` |