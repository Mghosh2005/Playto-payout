from django.urls import path
from . import views

urlpatterns = [
    path('payouts/', views.PayoutCreateView.as_view()),
    path('payouts/list/', views.PayoutListView.as_view()),
    path('payouts/<uuid:payout_id>/', views.PayoutDetailView.as_view()),
]
