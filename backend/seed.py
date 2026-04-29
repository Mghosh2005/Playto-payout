#!/usr/bin/env python
"""
Seed script: creates 3 merchants with bank accounts and credit history.
Run: python manage.py shell < seed.py
OR: python seed.py (from backend dir with DJANGO_SETTINGS_MODULE set)
"""
import os
import django
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from merchants.models import Merchant, BankAccount, LedgerEntry

MERCHANTS = [
    {
        'name': 'Arjun Design Studio',
        'email': 'arjun@designstudio.in',
        'bank': {
            'account_number': '1234567890123456',
            'ifsc_code': 'HDFC0001234',
            'account_holder_name': 'Arjun Sharma',
        },
        'credits': [
            (500_00, 'Payment from Client A (Invoice #001)'),
            (1200_00, 'Payment from Client B (Invoice #002)'),
            (750_00, 'Payment from Client C (Invoice #003)'),
        ]
    },
    {
        'name': 'Priya Freelance Writing',
        'email': 'priya@freelancewrite.in',
        'bank': {
            'account_number': '9876543210987654',
            'ifsc_code': 'ICIC0005678',
            'account_holder_name': 'Priya Nair',
        },
        'credits': [
            (300_00, 'Article payment from TechBlog (Jan)'),
            (450_00, 'Article payment from StartupMag (Jan)'),
            (600_00, 'Content project from E-commerce Co (Feb)'),
            (225_00, 'Article payment from TechBlog (Feb)'),
        ]
    },
    {
        'name': 'CodeForge Solutions',
        'email': 'hello@codeforge.in',
        'bank': {
            'account_number': '1122334455667788',
            'ifsc_code': 'SBIN0009012',
            'account_holder_name': 'Rohan Mehta',
        },
        'credits': [
            (5000_00, 'Project milestone 1 from US Client'),
            (5000_00, 'Project milestone 2 from US Client'),
            (2500_00, 'Maintenance retainer Feb'),
            (1800_00, 'Emergency support contract'),
        ]
    },
]

print("Seeding merchants...")

for data in MERCHANTS:
    merchant, created = Merchant.objects.get_or_create(
        email=data['email'],
        defaults={'name': data['name']}
    )
    if created:
        print(f"  Created merchant: {merchant.name}")
    else:
        print(f"  Merchant already exists: {merchant.name}")

    bank, _ = BankAccount.objects.get_or_create(
        merchant=merchant,
        account_number=data['bank']['account_number'],
        defaults={
            'ifsc_code': data['bank']['ifsc_code'],
            'account_holder_name': data['bank']['account_holder_name'],
        }
    )

    for amount, description in data['credits']:
        LedgerEntry.objects.create(
            merchant=merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=amount,
            description=description,
        )

    balance = merchant.get_balance_paise()
    print(f"    Balance: ₹{balance / 100:.2f} ({balance} paise)")

print("\nSeed complete!")
print("\nMerchant IDs:")
for m in Merchant.objects.all():
    bank = m.bank_accounts.first()
    print(f"  {m.name}")
print(f"    ID: {m.id}")
if bank:
    print(f"    Bank Account ID: {bank.id}")
print(f"    Balance: ₹{m.get_balance_paise() / 100:.2f}")
