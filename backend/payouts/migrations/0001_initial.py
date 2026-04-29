from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('merchants', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Payout',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount_paise', models.BigIntegerField()),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed')],
                    default='pending',
                    max_length=20,
                )),
                ('attempts', models.IntegerField(default=0)),
                ('max_attempts', models.IntegerField(default=3)),
                ('failure_reason', models.TextField(blank=True, null=True)),
                ('processing_started_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payouts', to='merchants.merchant')),
                ('bank_account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='merchants.bankaccount')),
            ],
            options={
                'db_table': 'payouts',
            },
        ),
        migrations.CreateModel(
            name='IdempotencyKey',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('key', models.CharField(max_length=255)),
                ('response_data', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='merchants.merchant')),
                ('payout', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='idempotency_keys', to='payouts.payout')),
            ],
            options={
                'db_table': 'idempotency_keys',
            },
        ),
        migrations.AddIndex(
            model_name='payout',
            index=models.Index(fields=['merchant', 'status'], name='payouts_merchant_status_idx'),
        ),
        migrations.AddIndex(
            model_name='payout',
            index=models.Index(fields=['status', 'processing_started_at'], name='payouts_status_proc_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='idempotencykey',
            unique_together={('merchant', 'key')},
        ),
        migrations.AddIndex(
            model_name='idempotencykey',
            index=models.Index(fields=['merchant', 'key'], name='idem_merchant_key_idx'),
        ),
    ]
