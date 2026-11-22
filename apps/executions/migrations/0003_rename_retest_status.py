from django.db import migrations, models


def rename_retest_status(apps, schema_editor):
    """将已有执行记录中的重测状态统一迁移为跳过状态。"""
    TestRunCase = apps.get_model('executions', 'TestRunCase')
    TestRunCaseHistory = apps.get_model('executions', 'TestRunCaseHistory')
    TestRunCase.objects.filter(status='retest').update(status='skipped')
    TestRunCaseHistory.objects.filter(status='retest').update(status='skipped')


class Migration(migrations.Migration):

    dependencies = [
        ('executions', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(rename_retest_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='testruncase',
            name='status',
            field=models.CharField(
                choices=[
                    ('untested', '未测试'),
                    ('passed', '通过'),
                    ('failed', '失败'),
                    ('blocked', '阻塞'),
                    ('skipped', '跳过'),
                ],
                default='untested',
                max_length=20,
                verbose_name='执行状态',
            ),
        ),
        migrations.AlterField(
            model_name='testruncasehistory',
            name='status',
            field=models.CharField(
                choices=[
                    ('untested', '未测试'),
                    ('passed', '通过'),
                    ('failed', '失败'),
                    ('blocked', '阻塞'),
                    ('skipped', '跳过'),
                ],
                max_length=20,
                verbose_name='执行状态',
            ),
        ),
    ]
