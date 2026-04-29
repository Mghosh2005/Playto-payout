# EXPLAINER.md

> Answers to the five questions in the challenge spec.
> This is where most candidates get filtered. Short and specific.

---

## 1. The Ledger

**Balance calculation query (from `payouts/views.py`):**

```python
from django.db.models import Sum, Q

agg = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
    credits=Sum('amount_paise', filter=Q(entry_type='credit')),
    debits=Sum('amount_paise', filter=Q(entry_type='debit'))
)
available = (agg['credits'] or 0) - (agg['debits'] or 0)
```

This emits a single SQL query:
```sql
SELECT
    SUM(amount_paise) FILTER (WHERE entry_type = 'credit') AS credits,
    SUM(amount_paise) FILTER (WHERE entry_type = 'debit')  AS debits
FROM ledger_entries
WHERE merchant_id = %s;
```

**Why this model:**

Credits and debits are separate rows in a single `ledger_entries` table — not a mutable `balance` column on the merchant. Every financial event appends a row; nothing is ever updated or deleted. This is the double-entry pattern used by every real payment system.

Benefits:
- **Auditability**: you can replay the ledger to any point in time. A mutable balance column loses history.
- **Invariant checkability**: `SUM(credits) - SUM(debits)` must always equal the displayed balance. We can verify this at any time with a single query.
- **No race condition on reads**: computing from the ledger inside a locked transaction gives a consistent view. A cached balance column is stale by definition.
- **No floats**: `BigIntegerField` storing paise (integer ÷ 100 = rupees). `FloatField` loses precision on values like ₹12.30 (1230 paise stored as 1229.9999…). `DecimalField` would also work but adds ORM overhead with no benefit since our only arithmetic is integer addition and subtraction.

---

## 2. The Lock

**Exact code from `payouts/views.py`:**

```python
with transaction.atomic():
    # LOCK: SELECT ... FOR UPDATE on the merchant row.
    # Any other transaction that tries to SELECT FOR UPDATE on this
    # same merchant row will BLOCK here until we commit or rollback.
    locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)

    # Balance computed at DB level inside the locked transaction
    agg = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
        credits=Sum('amount_paise', filter=Q(entry_type='credit')),
        debits=Sum('amount_paise', filter=Q(entry_type='debit'))
    )
    available = (agg['credits'] or 0) - (agg['debits'] or 0)

    if available < amount_paise:
        return Response({'error': 'Insufficient balance'}, status=422)

    # Payout creation + ledger debit happen here, inside same transaction
    payout = Payout.objects.create(merchant=locked_merchant, ...)
    LedgerEntry.objects.create(entry_type='debit', ...)
    # Transaction commits here → lock released
```

**Database primitive: `SELECT FOR UPDATE` (PostgreSQL row-level exclusive lock)**

Without this lock, the following race condition is possible:

```
T1: reads balance = 10000          T2: reads balance = 10000
T1: 10000 >= 6000 → OK             T2: 10000 >= 6000 → OK
T1: creates payout, debits 6000    T2: creates payout, debits 6000
T1: commits (balance now 4000)     T2: commits (balance now -2000) ← OVERDRAFT
```

With `SELECT FOR UPDATE`:
```
T1: SELECT merchant FOR UPDATE → acquires row lock
T2: SELECT merchant FOR UPDATE → BLOCKS (waits for T1)
T1: reads balance = 10000, OK, deducts 6000, commits → lock released
T2: unblocks, reads balance = 4000, 4000 < 6000 → 422 Rejected
```

Python-level locks (`threading.Lock`) do not work here because requests run in separate OS processes (gunicorn workers). A Python lock in one process is invisible to another. The lock must live at the database level.

---

## 3. The Idempotency

**How the system recognises a key it has seen before:**

`IdempotencyKey` has a `unique_together = [('merchant', 'key')]` constraint enforced at the PostgreSQL level. The flow:

1. **First request**: `get_or_create(merchant=m, key=k)` → `created=True`. We create the payout, serialize the response, store it in `response_data` on the same row. All inside `transaction.atomic()`.

2. **Second request (key already committed)**: `get_or_create` → `created=False`. We return `existing_key.response_data` immediately. No new payout created.

3. **Keys are scoped per merchant**: the unique constraint is on `(merchant_id, key)` not just `key`. Merchant A and Merchant B can use the same UUID key independently.

4. **Keys expire**: we filter `created_at >= now - 24h` before lookup. An expired key is treated as unseen.

**What happens if the first request is still in flight when the second arrives:**

The `IdempotencyKey` row is inserted inside the same `atomic()` block as the payout. Two scenarios:

- **T1 not yet committed**: T2's `get_or_create` attempts to insert the same `(merchant, key)`. PostgreSQL blocks T2 until T1 commits (row lock from the unique index). Once T1 commits, T2 gets `created=False` and returns T1's response.

- **T1 rolled back** (crash, exception after key insert but before commit): PostgreSQL rolls back T1's row. T2's `get_or_create` inserts fresh → `created=True` → proceeds as first request. Correct.

- **True simultaneous insert race** (both reach `get_or_create` before either commits): one gets `IntegrityError` from the unique constraint. We catch that, fetch the winner's row, and return their `response_data`. See the `except IntegrityError` block in `views.py`.

---

## 4. The State Machine

**Where `failed → completed` (and all illegal transitions) are blocked:**

`payouts/models.py`, `Payout.transition_to()`:

```python
LEGAL_TRANSITIONS = {
    'pending':    ['processing'],
    'processing': ['completed', 'failed'],
    'completed':  [],   # terminal — empty list means nothing is allowed
    'failed':     [],   # terminal — empty list means nothing is allowed
}

def transition_to(self, new_status):
    allowed = self.LEGAL_TRANSITIONS.get(self.status, [])
    if new_status not in allowed:
        raise ValueError(
            f"Illegal state transition: {self.status} → {new_status}. "
            f"Allowed from '{self.status}': {allowed}"
        )
    self.status = new_status
    if new_status == self.PROCESSING:
        self.processing_started_at = timezone.now()
```

Every status change anywhere in the codebase goes through `transition_to()`. Direct assignment `payout.status = 'completed'` never appears outside this method. Illegal transitions raise `ValueError` before the model is saved — the exception propagates up through `transaction.atomic()`, rolling back any partial changes.

For the refund path specifically: when a payout fails, the funds are returned to the merchant balance **in the same transaction** as the state transition:

```python
with transaction.atomic():
    payout = Payout.objects.select_for_update().get(id=payout_id)
    payout.transition_to(Payout.FAILED)   # raises if not in PROCESSING
    payout.save(...)
    LedgerEntry.objects.create(           # credit is atomic with state change
        entry_type='credit',
        amount_paise=payout.amount_paise,
        description=f'Funds returned for failed payout {payout.id}',
        ...
    )
    # Both writes commit together or neither does
```

This atomicity means it is impossible for a payout to be `failed` without its refund credit appearing in the ledger, and vice versa.

---

## 5. The AI Audit

**What Copilot generated when I asked for the concurrent balance check:**

```python
# AI output — WRONG
def create_payout_view(request):
    merchant = Merchant.objects.get(id=request.data['merchant_id'])
    balance = merchant.get_balance_paise()      # Python int returned from DB query

    if balance >= amount_paise:                  # check in Python
        payout = Payout.objects.create(...)     # write in separate DB call
        LedgerEntry.objects.create(
            entry_type='debit',
            amount_paise=amount_paise,
            ...
        )
        return Response(...)
```

**What's wrong:**

This is a classic TOCTOU (Time of Check to Time of Use) race condition. `get_balance_paise()` executes a SELECT, returns a Python integer, then the function closes the DB connection for that query. The `if balance >= amount_paise` check and the `Payout.objects.create()` write are two completely separate database operations with no atomicity guarantee between them.

Between those two operations, another gunicorn worker handling a concurrent request can execute its own SELECT, see the same balance, pass the same check, and proceed to debit the same funds. Both workers create payouts. Both debit. The merchant goes negative.

Additionally, `get_balance_paise()` is called *before* any transaction is open. Even wrapping the `if + create` in `atomic()` would not help — the read that informed the decision happened outside the transaction boundary.

**What I replaced it with:**

The read and write are fused inside a single `transaction.atomic()` block, with `SELECT FOR UPDATE` on the merchant row preceding the balance aggregate:

```python
with transaction.atomic():
    locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)
    agg = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
        credits=Sum('amount_paise', filter=Q(entry_type='credit')),
        debits=Sum('amount_paise', filter=Q(entry_type='debit'))
    )
    available = (agg['credits'] or 0) - (agg['debits'] or 0)
    if available < amount_paise:
        return Response({'error': 'Insufficient balance'}, status=422)
    # payout + debit created here, still inside same transaction
```

The `SELECT FOR UPDATE` acquires an exclusive row lock on the merchant. Any concurrent transaction reaching the same `SELECT FOR UPDATE` blocks at the database level — not the Python level — until this transaction commits or rolls back. The check and the deduct are now atomic. Two concurrent requests for 6000p against a 10000p balance will serialize: one succeeds, one gets 422.
