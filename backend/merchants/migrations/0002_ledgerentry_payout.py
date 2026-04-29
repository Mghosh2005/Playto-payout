from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('merchants', '0001_initial'),
        ('payouts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ledgerentry',
            name='payout',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='ledger_entries',
                to='payouts.payout',
            ),
        ),
    ]