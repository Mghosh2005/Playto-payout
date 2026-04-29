from django.urls import path
from . import views

urlpatterns = [
    path('merchants/', views.MerchantListView.as_view()),
    path('merchants/<uuid:merchant_id>/', views.MerchantDashboardView.as_view()),
    path('merchants/<uuid:merchant_id>/ledger/', views.MerchantLedgerView.as_view()),
]
