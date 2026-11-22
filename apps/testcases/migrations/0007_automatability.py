from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('testcases', '0004_active_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='testcase',
            name='automatability',
            field=models.BooleanField(default=True, verbose_name='是否能自动化'),
        ),
    ]
