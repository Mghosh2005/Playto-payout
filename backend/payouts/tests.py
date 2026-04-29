import threading
import uuid
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from merchants.models import Merchant, BankAccount, LedgerEntry
from payouts.models import Payout, IdempotencyKey


class ConcurrencyTest(TransactionTestCase):
    """
    Two simultaneous 6000p payout requests against a 10000p balance.
    Exactly one must succeed (201), one must be rejected (422).

    MUST use TransactionTestCase — not TestCase.
    TestCase wraps every test in a savepoint/transaction, which means
    SELECT FOR UPDATE becomes a no-op (you can't lock within your own
    transaction). TransactionTestCase flushes the DB between tests and
    allows real concurrent transactions.
    """

    def setUp(self):
        self.merchant = Merchant.objects.create(
            name='Concurrency Test Merchant',
            email='concurrent@test.com'
        )
        self.bank = BankAccount.objects.create(
            merchant=self.merchant,
            account_number='1234567890',
            ifsc_code='HDFC0001',
            account_holder_name='Test User'
        )
        # Fund with 10000 paise (₹100)
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=10000,
            description='Test seed credit'
        )

    def test_concurrent_overdraw_rejected(self):
        """
        Two simultaneous 6000p requests (total 12000p) against 10000p balance.
        Exactly one 201, exactly one 422. Balance never goes negative.
        """
        client = APIClient()
        results = []
        lock = threading.Lock()

        def make_request():
            resp = client.post(
                '/api/v1/payouts/',
                {
                    'merchant_id': str(self.merchant.id),
                    'amount_paise': 6000,
                    'bank_account_id': str(self.bank.id),
                },
                headers={'Idempotency-Key': str(uuid.uuid4())},
                format='json'
            )
            with lock:
                results.append(resp.status_code)

        t1 = threading.Thread(target=make_request)
        t2 = threading.Thread(target=make_request)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(
            sorted(results), [201, 422],
            f"Expected [201, 422] but got {sorted(results)}. "
            f"Race condition not prevented!"
        )

        # Ledger invariant: balance must never be negative
        final_balance = self.merchant.get_balance_paise()
        self.assertGreaterEqual(
            final_balance, 0,
            f"Balance went negative! Got {final_balance} paise"
        )

        # Only one payout should exist
        self.assertEqual(
            Payout.objects.filter(merchant=self.merchant).count(), 1
        )


class IdempotencyTest(TestCase):
    """
    Same Idempotency-Key header must return the same payout, no duplicates.
    """

    def setUp(self):
        self.merchant = Merchant.objects.create(
            name='Idempotency Test Merchant',
            email='idem@test.com'
        )
        self.bank = BankAccount.objects.create(
            merchant=self.merchant,
            account_number='9876543210',
            ifsc_code='ICIC0001',
            account_holder_name='Idem User'
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=50000,
            description='Test seed credit'
        )
        self.client = APIClient()

    def test_same_key_returns_same_payout_id(self):
        """Calling POST twice with the same key must return the exact same payout."""
        key = str(uuid.uuid4())
        payload = {
            'merchant_id': str(self.merchant.id),
            'amount_paise': 1000,
            'bank_account_id': str(self.bank.id),
        }

        r1 = self.client.post(
            '/api/v1/payouts/', payload,
            headers={'Idempotency-Key': key}, format='json'
        )
        r2 = self.client.post(
            '/api/v1/payouts/', payload,
            headers={'Idempotency-Key': key}, format='json'
        )

        self.assertIn(r1.status_code, [200, 201])
        self.assertIn(r2.status_code, [200, 201])
        self.assertEqual(
            r1.data['id'], r2.data['id'],
            "Same idempotency key returned different payout IDs!"
        )
        # Only one payout record in DB
        self.assertEqual(
            Payout.objects.filter(merchant=self.merchant).count(), 1
        )

    def test_different_keys_create_separate_payouts(self):
        """Different keys must create independent payouts."""
        payload = {
            'merchant_id': str(self.merchant.id),
            'amount_paise': 1000,
            'bank_account_id': str(self.bank.id),
        }

        r1 = self.client.post(
            '/api/v1/payouts/', payload,
            headers={'Idempotency-Key': str(uuid.uuid4())}, format='json'
        )
        r2 = self.client.post(
            '/api/v1/payouts/', payload,
            headers={'Idempotency-Key': str(uuid.uuid4())}, format='json'
        )

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertNotEqual(r1.data['id'], r2.data['id'])
        self.assertEqual(Payout.objects.filter(merchant=self.merchant).count(), 2)

    def test_key_scoped_per_merchant(self):
        """Same key used by two different merchants should create two separate payouts."""
        merchant2 = Merchant.objects.create(name='Merchant 2', email='m2@test.com')
        bank2 = BankAccount.objects.create(
            merchant=merchant2, account_number='1111222233334444',
            ifsc_code='SBIN0001', account_holder_name='M2 User'
        )
        LedgerEntry.objects.create(
            merchant=merchant2, entry_type=LedgerEntry.CREDIT,
            amount_paise=50000, description='Seed'
        )

        shared_key = str(uuid.uuid4())

        r1 = self.client.post('/api/v1/payouts/', {
            'merchant_id': str(self.merchant.id),
            'amount_paise': 1000,
            'bank_account_id': str(self.bank.id),
        }, headers={'Idempotency-Key': shared_key}, format='json')

        r2 = self.client.post('/api/v1/payouts/', {
            'merchant_id': str(merchant2.id),
            'amount_paise': 1000,
            'bank_account_id': str(bank2.id),
        }, headers={'Idempotency-Key': shared_key}, format='json')

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertNotEqual(r1.data['id'], r2.data['id'])

    def test_missing_idempotency_key_rejected(self):
        """Request without Idempotency-Key header must be rejected."""
        r = self.client.post('/api/v1/payouts/', {
            'merchant_id': str(self.merchant.id),
            'amount_paise': 1000,
            'bank_account_id': str(self.bank.id),
        }, format='json')
        self.assertEqual(r.status_code, 400)

    def test_insufficient_balance_rejected(self):
        """Request exceeding balance must return 422."""
        r = self.client.post('/api/v1/payouts/', {
            'merchant_id': str(self.merchant.id),
            'amount_paise': 999999999,
            'bank_account_id': str(self.bank.id),
        }, headers={'Idempotency-Key': str(uuid.uuid4())}, format='json')
        self.assertEqual(r.status_code, 422)

    def test_state_machine_blocks_illegal_transitions(self):
        """completed → pending and failed → completed must raise ValueError."""
        from payouts.models import Payout

        payout = Payout(status=Payout.COMPLETED)
        with self.assertRaises(ValueError):
            payout.transition_to(Payout.PENDING)

        payout2 = Payout(status=Payout.FAILED)
        with self.assertRaises(ValueError):
            payout2.transition_to(Payout.COMPLETED)

        payout3 = Payout(status=Payout.PROCESSING)
        with self.assertRaises(ValueError):
            payout3.transition_to(Payout.PENDING)
