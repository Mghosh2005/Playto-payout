import uuid
from django.db import models
from django.utils import timezone


class IdempotencyKey(models.Model):
    """
    Stores idempotency keys scoped per merchant.
    A key maps to exactly one payout response.
    Keys expire after 24 hours (checked at lookup time).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey('merchants.Merchant', on_delete=models.CASCADE)
    key = models.CharField(max_length=255)
    payout = models.ForeignKey(
        'Payout',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='idempotency_keys'
    )
    # Store the serialized response so we can return exact same response
    response_data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'idempotency_keys'
        # Enforce uniqueness at DB level: (merchant_id, key) must be unique
        unique_together = [('merchant', 'key')]
        indexes = [
            models.Index(fields=['merchant', 'key']),
        ]


class Payout(models.Model):
    # --- State machine ---
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (PROCESSING, 'Processing'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
    ]

    # Legal transitions only:
    # pending → processing → completed
    # pending → processing → failed
    # Anything else is illegal and must be rejected in code.
    LEGAL_TRANSITIONS = {
        PENDING: [PROCESSING],
        PROCESSING: [COMPLETED, FAILED],
        COMPLETED: [],   # terminal state — no transitions allowed
        FAILED: [],      # terminal state — no transitions allowed
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey('merchants.Merchant', on_delete=models.CASCADE, related_name='payouts')
    bank_account = models.ForeignKey('merchants.BankAccount', on_delete=models.PROTECT)
    amount_paise = models.BigIntegerField()  # NEVER FloatField
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)
    failure_reason = models.TextField(null=True, blank=True)
    # Track when we moved to processing for stuck-payout detection
    processing_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def transition_to(self, new_status):
        """
        Enforce state machine transitions.
        Raises ValueError for illegal transitions.
        This is the ONLY place status changes should happen.
        """
        allowed = self.LEGAL_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Illegal state transition: {self.status} → {new_status}. "
                f"Allowed from {self.status}: {allowed}"
            )
        self.status = new_status
        if new_status == self.PROCESSING:
            self.processing_started_at = timezone.now()

    def __str__(self):
        return f"Payout {self.id} | {self.merchant.name} | {self.amount_paise}p | {self.status}"

    class Meta:
        db_table = 'payouts'
        indexes = [
            models.Index(fields=['merchant', 'status']),
            models.Index(fields=['status', 'processing_started_at']),
        ]
