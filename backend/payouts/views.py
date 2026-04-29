import uuid
import logging
from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import json
from merchants.models import Merchant, BankAccount, LedgerEntry
from .models import Payout, IdempotencyKey
from .serializers import PayoutSerializer
from .tasks import process_payout

logger = logging.getLogger(__name__)


class PayoutCreateView(APIView):
    """
    POST /api/v1/payouts
    Headers: Idempotency-Key: <uuid>
    Body: { amount_paise: int, bank_account_id: uuid, merchant_id: uuid }

    Concurrency guarantee:
    - SELECT FOR UPDATE on merchant's ledger aggregate inside a transaction
      ensures check-then-deduct is atomic at the DB level, not Python level.

    Idempotency guarantee:
    - unique_together constraint on (merchant, key) + get_or_create means
      a second request with the same key returns the stored response.
    """

    def post(self, request):
        idempotency_key = request.headers.get('Idempotency-Key')
        if not idempotency_key:
            return Response(
                {'error': 'Idempotency-Key header is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate UUID format
        try:
            uuid.UUID(str(idempotency_key))
        except ValueError:
            return Response(
                {'error': 'Idempotency-Key must be a valid UUID'},
                status=status.HTTP_400_BAD_REQUEST
            )

        merchant_id = request.data.get('merchant_id')
        amount_paise = request.data.get('amount_paise')
        bank_account_id = request.data.get('bank_account_id')

        if not all([merchant_id, amount_paise, bank_account_id]):
            return Response(
                {'error': 'merchant_id, amount_paise, and bank_account_id are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            amount_paise = int(amount_paise)
        except (TypeError, ValueError):
            return Response(
                {'error': 'amount_paise must be an integer'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount_paise <= 0:
            return Response(
                {'error': 'amount_paise must be positive'},
                status=status.HTTP_400_BAD_REQUEST
            )

        merchant = get_object_or_404(Merchant, id=merchant_id)
        bank_account = get_object_or_404(BankAccount, id=bank_account_id, merchant=merchant, is_active=True)

        # --- Idempotency check ---
        # Check if this key already exists for this merchant
        # Keys expire after IDEMPOTENCY_KEY_TTL seconds
        ttl_cutoff = timezone.now() - timezone.timedelta(seconds=getattr(settings, 'IDEMPOTENCY_KEY_TTL', 86400))

        existing_key = IdempotencyKey.objects.filter(
            merchant=merchant,
            key=idempotency_key,
            created_at__gte=ttl_cutoff
        ).select_related('payout').first()

        if existing_key is not None:
            # Key seen before — return exact same response
            # This handles the "first request still in flight" case:
            # if payout was created but response_data not yet set,
            # we return the payout's current state anyway.
            if existing_key.response_data:
                return Response(existing_key.response_data, status=status.HTTP_200_OK)
            elif existing_key.payout:
                serializer = PayoutSerializer(existing_key.payout)
                return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                # Edge case: key exists but no payout linked (creation was interrupted)
                # Fall through to create new payout for this key
                pass

        # --- Transactional balance check + payout creation ---
        # The entire check-then-deduct must be atomic.
        # We use SELECT FOR UPDATE to lock the merchant row first,
        # then compute balance from ledger. This prevents two concurrent
        # requests from both seeing "sufficient balance" before either deducts.
        try:
            with transaction.atomic():
                # STEP 1: Lock the merchant row.
                # SELECT ... FOR UPDATE prevents any other transaction from
                # acquiring the same lock until this transaction commits or rolls back.
                # This is the database primitive that prevents the race condition.
                locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)

                # STEP 2: Compute balance at DB level (not Python arithmetic on fetched rows)
                from django.db.models import Sum, Q
                agg = LedgerEntry.objects.filter(merchant=locked_merchant).aggregate(
                    credits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.CREDIT)),
                    debits=Sum('amount_paise', filter=Q(entry_type=LedgerEntry.DEBIT))
                )
                credits = agg['credits'] or 0
                debits = agg['debits'] or 0
                available_balance = credits - debits

                if available_balance < amount_paise:
                    return Response(
                        {
                            'error': 'Insufficient balance',
                            'available_paise': available_balance,
                            'requested_paise': amount_paise,
                        },
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY
                    )

                # STEP 3: Create payout record
                payout = Payout.objects.create(
                    merchant=locked_merchant,
                    bank_account=bank_account,
                    amount_paise=amount_paise,
                    status=Payout.PENDING,
                )

                # STEP 4: Debit ledger immediately (holds the funds)
                LedgerEntry.objects.create(
                    merchant=locked_merchant,
                    entry_type=LedgerEntry.DEBIT,
                    amount_paise=amount_paise,
                    description=f'Payout hold for payout {payout.id}',
                    payout=payout,
                )

                # STEP 5: Record idempotency key
                # Use get_or_create with the unique_together constraint as the guard.
                # If a concurrent request already inserted the same key, this raises
                # IntegrityError which we catch below.
                idem_key, created = IdempotencyKey.objects.get_or_create(
                    merchant=locked_merchant,
                    key=idempotency_key,
                    defaults={'payout': payout}
                )

                if not created:
                    # Another concurrent request with the same key completed first.
                    # Roll back this transaction and return their response.
                    # Raising here causes the atomic() block to rollback.
                    raise IntegrityError("Concurrent idempotency key conflict")

                serializer = PayoutSerializer(payout)
                response_data = serializer.data

                # Store response for future idempotent replays
                idem_key.response_data = json.loads(json.dumps(dict(response_data), default=str))
                idem_key.save(update_fields=['response_data'])

        except IntegrityError:
            # Idempotency key was inserted by a concurrent request.
            # Fetch and return their response.
            existing_key = IdempotencyKey.objects.filter(
                merchant=merchant,
                key=idempotency_key,
            ).select_related('payout').first()
            if existing_key and existing_key.response_data:
                return Response(existing_key.response_data, status=status.HTTP_200_OK)
            return Response({'error': 'Concurrent request in progress, retry'}, status=status.HTTP_409_CONFLICT)

        # Dispatch background processing (outside the transaction to avoid holding locks)
        process_payout.apply_async(args=[str(payout.id)], countdown=1)

        return Response(response_data, status=status.HTTP_201_CREATED)


class PayoutListView(APIView):
    def get(self, request):
        merchant_id = request.query_params.get('merchant_id')
        if not merchant_id:
            return Response({'error': 'merchant_id query param required'}, status=status.HTTP_400_BAD_REQUEST)

        payouts = Payout.objects.filter(merchant_id=merchant_id).order_by('-created_at')[:50]
        serializer = PayoutSerializer(payouts, many=True)
        return Response(serializer.data)


class PayoutDetailView(APIView):
    def get(self, request, payout_id):
        payout = get_object_or_404(Payout, id=payout_id)
        serializer = PayoutSerializer(payout)
        return Response(serializer.data)
