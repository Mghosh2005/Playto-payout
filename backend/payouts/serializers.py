from rest_framework import serializers
from .models import Payout


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = [
            'id', 'merchant_id', 'bank_account_id',
            'amount_paise', 'status', 'attempts',
            'failure_reason', 'created_at', 'updated_at'
        ]
