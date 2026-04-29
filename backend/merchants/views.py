from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from .models import Merchant, LedgerEntry
from .serializers import MerchantDashboardSerializer, LedgerEntrySerializer, MerchantSerializer


class MerchantListView(APIView):
    def get(self, request):
        merchants = Merchant.objects.all()
        serializer = MerchantSerializer(merchants, many=True)
        return Response(serializer.data)


class MerchantDashboardView(APIView):
    def get(self, request, merchant_id):
        merchant = get_object_or_404(Merchant, id=merchant_id)
        serializer = MerchantDashboardSerializer(merchant)
        return Response(serializer.data)


class MerchantLedgerView(APIView):
    def get(self, request, merchant_id):
        merchant = get_object_or_404(Merchant, id=merchant_id)
        entries = LedgerEntry.objects.filter(merchant=merchant).select_related('payout')[:50]
        serializer = LedgerEntrySerializer(entries, many=True)
        return Response(serializer.data)
