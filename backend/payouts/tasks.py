import random
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Stuck payout threshold: 30 seconds
STUCK_THRESHOLD_SECONDS = 30
# Exponential backoff base (seconds)
BACKOFF_BASE = 2


@shared_task(bind=True, max_retries=3)
def process_payout(self, payout_id: str):
    """
    Pick up a pending payout and run it through the lifecycle.
    Simulated bank outcomes:
      - 70% success  → completed
      - 20% failure  → failed, funds returned
      - 10% hang     → stays in processing (retry_stuck_payouts will catch it)
    """
    from payouts.models import Payout
    from merchants.models import LedgerEntry

    try:
        with transaction.atomic():
            # Lock the payout row to prevent concurrent processing
            payout = Payout.objects.select_for_update().get(id=payout_id)

            if payout.status != Payout.PENDING:
                logger.info(f"Payout {payout_id} is not pending (status={payout.status}), skipping")
                return

            # Transition to processing
            payout.attempts += 1
            payout.transition_to(Payout.PROCESSING)
            payout.save(update_fields=['status', 'attempts', 'processing_started_at', 'updated_at'])

    except Payout.DoesNotExist:
        logger.error(f"Payout {payout_id} not found")
        return
    except ValueError as e:
        logger.error(f"Invalid state transition for payout {payout_id}: {e}")
        return

    # Simulate bank processing (outside the transaction that sets PROCESSING
    # so we don't hold a lock during the simulated network call)
    outcome = _simulate_bank_outcome()
    logger.info(f"Payout {payout_id}: bank outcome = {outcome}")

    if outcome == 'hang':
        # Leave in PROCESSING; retry_stuck_payouts will pick it up after 30s
        logger.info(f"Payout {payout_id} hung in processing, will be retried by scheduler")
        return

    with transaction.atomic():
        # Re-lock before final state transition
        payout = Payout.objects.select_for_update().get(id=payout_id)

        if payout.status != Payout.PROCESSING:
            # Something else already transitioned it (e.g. retry job)
            logger.info(f"Payout {payout_id} status changed to {payout.status} before we could finalize")
            return

        if outcome == 'success':
            payout.transition_to(Payout.COMPLETED)
            payout.save(update_fields=['status', 'updated_at'])
            logger.info(f"Payout {payout_id} completed successfully")

        elif outcome == 'failure':
            payout.transition_to(Payout.FAILED)
            payout.failure_reason = 'Bank transfer rejected'
            payout.save(update_fields=['status', 'failure_reason', 'updated_at'])

            # ATOMICALLY return funds: the state transition and credit happen in the same transaction
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.CREDIT,
                amount_paise=payout.amount_paise,
                description=f'Funds returned for failed payout {payout.id}',
                payout=payout,
            )
            logger.info(f"Payout {payout_id} failed, funds returned to merchant")


@shared_task
def retry_stuck_payouts():
    """
    Periodic task (every 30s via beat) that finds payouts stuck in PROCESSING
    longer than STUCK_THRESHOLD_SECONDS and retries them with exponential backoff.
    After max_attempts, moves to FAILED and returns funds.
    """
    from payouts.models import Payout
    from merchants.models import LedgerEntry

    stuck_cutoff = timezone.now() - timezone.timedelta(seconds=STUCK_THRESHOLD_SECONDS)

    stuck_payouts = Payout.objects.filter(
        status=Payout.PROCESSING,
        processing_started_at__lte=stuck_cutoff,
    ).select_for_update(skip_locked=True)  # skip_locked=True: don't wait for locked rows

    with transaction.atomic():
        for payout in stuck_payouts:
            if payout.attempts >= payout.max_attempts:
                # Exhaust retries: fail and return funds atomically
                try:
                    payout.transition_to(Payout.FAILED)
                    payout.failure_reason = f'Exceeded max attempts ({payout.max_attempts})'
                    payout.save(update_fields=['status', 'failure_reason', 'updated_at'])

                    LedgerEntry.objects.create(
                        merchant=payout.merchant,
                        entry_type=LedgerEntry.CREDIT,
                        amount_paise=payout.amount_paise,
                        description=f'Funds returned for timed-out payout {payout.id}',
                        payout=payout,
                    )
                    logger.info(f"Stuck payout {payout.id} failed after max retries, funds returned")
                except ValueError as e:
                    logger.error(f"State transition error for stuck payout {payout.id}: {e}")
            else:
                # Reset to pending for retry with exponential backoff
                # Transition back pending is not in the legal state machine —
                # instead we schedule a fresh processing attempt directly
                backoff = BACKOFF_BASE ** payout.attempts
                logger.info(f"Retrying stuck payout {payout.id} with backoff {backoff}s")

                # Re-queue processing task
                # Note: we keep status as PROCESSING and let process_payout_retry handle it
                process_payout_retry.apply_async(args=[str(payout.id)], countdown=backoff)


@shared_task(bind=True, max_retries=3)
def process_payout_retry(self, payout_id: str):
    """
    Retry handler for stuck payouts. Differs from process_payout:
    picks up from PROCESSING state rather than PENDING.
    """
    from payouts.models import Payout
    from merchants.models import LedgerEntry

    outcome = _simulate_bank_outcome()
    logger.info(f"Payout {payout_id} retry: bank outcome = {outcome}")

    if outcome == 'hang':
        # Will be caught by retry_stuck_payouts again
        return

    with transaction.atomic():
        payout = Payout.objects.select_for_update().get(id=payout_id)

        if payout.status != Payout.PROCESSING:
            return

        payout.attempts += 1

        if outcome == 'success':
            payout.transition_to(Payout.COMPLETED)
            payout.save(update_fields=['status', 'attempts', 'updated_at'])

        elif outcome == 'failure':
            payout.transition_to(Payout.FAILED)
            payout.failure_reason = 'Bank transfer rejected on retry'
            payout.save(update_fields=['status', 'failure_reason', 'attempts', 'updated_at'])

            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.CREDIT,
                amount_paise=payout.amount_paise,
                description=f'Funds returned for failed payout (retry) {payout.id}',
                payout=payout,
            )


def _simulate_bank_outcome() -> str:
    """70% success, 20% failure, 10% hang."""
    roll = random.random()
    if roll < 0.70:
        return 'success'
    elif roll < 0.90:
        return 'failure'
    else:
        return 'hang'
