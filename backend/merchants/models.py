import uuid
from django.db import models
from django.db.models import Sum, Q


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def get_balance_paise(self):
        """
        Derive balance purely from ledger entries using a single DB aggregation.
        Never fetch rows and sum in Python — do it at the DB level.
        credits - debits = available balance
        """
        result = LedgerEntry.objects.filter(merchant=self).aggregate(
            balance=Sum(
                'amount_paise',
                filter=Q(entry_type=LedgerEntry.CREDIT)
            ) - Sum(
                'amount_paise',
                filter=Q(entry_type=LedgerEntry.DEBIT)
            )
        )
        # Coalesce None (no entries) to 0
        balance = result['balance']
        if balance is None:
            # Check if there are credits at all
            credits = LedgerEntry.objects.filter(
                merchant=self, entry_type=LedgerEntry.CREDIT
            ).aggregate(total=Sum('amount_paise'))['total'] or 0
            debits = LedgerEntry.objects.filter(
                merchant=self, entry_type=LedgerEntry.DEBIT
            ).aggregate(total=Sum('amount_paise'))['total'] or 0
            balance = credits - debits
        return balance

    def get_held_paise(self):
        """
        Sum of pending payout amounts (funds reserved but not yet settled).
        """
        from payouts.models import Payout
        result = Payout.objects.filter(
            merchant=self,
            status__in=[Payout.PENDING, Payout.PROCESSING]
        ).aggregate(held=Sum('amount_paise'))
        return result['held'] or 0

    class Meta:
        db_table = 'merchants'


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='bank_accounts')
    account_number = models.CharField(max_length=20)
    ifsc_code = models.CharField(max_length=11)
    account_holder_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.account_holder_name} - {self.account_number[-4:].zfill(4)}"

    class Meta:
        db_table = 'bank_accounts'


class LedgerEntry(models.Model):
    CREDIT = 'credit'
    DEBIT = 'debit'
    ENTRY_TYPES = [(CREDIT, 'Credit'), (DEBIT, 'Debit')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='ledger_entries')
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPES)
    amount_paise = models.BigIntegerField()  # NEVER FloatField. BigInt for paise.
    description = models.CharField(max_length=500)
    # Reference to the payout that caused this entry (nullable for credits)
    payout = models.ForeignKey(
        'payouts.Payout',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='ledger_entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.entry_type} {self.amount_paise}p for {self.merchant.name}"

    class Meta:
        db_table = 'ledger_entries'
        ordering = ['-created_at']
