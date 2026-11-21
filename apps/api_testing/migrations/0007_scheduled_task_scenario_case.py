from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0006_migrate_scenario_case_requests'),
    ]

    operations = [
        migrations.AddField(
            model_name='requesthistory',
            name='scheduled_task',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                     related_name='request_histories', to='api_testing.scheduledtask',
                                     verbose_name='关联定时任务'),
        ),
        migrations.AddField(
            model_name='testexecution',
            name='scheduled_task',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                     related_name='scenario_executions', to='api_testing.scheduledtask',
                                     verbose_name='关联定时任务'),
        ),
        migrations.AddField(
            model_name='apisuiteexecution',
            name='scheduled_task',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                     related_name='suite_executions_by_task', to='api_testing.scheduledtask',
                                     verbose_name='关联定时任务'),
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='scenario_cases',
            field=models.JSONField(blank=True, default=list, help_text='场景用例ID列表，用于SCENARIO_CASE类型任务',
                                   verbose_name='场景用例列表'),
        ),
        migrations.AlterField(
            model_name='scheduledtask',
            name='task_type',
            field=models.CharField(
                choices=[
                    ('TEST_SUITE', '测试套件执行'),
                    ('API_REQUEST', 'API请求执行'),
                    ('SCENARIO_CASE', '场景用例执行'),
                ],
                max_length=20,
                verbose_name='任务类型',
            ),
        ),
        migrations.RemoveField(
            model_name='scheduledtask',
            name='api_request',
        ),
        migrations.AddField(
            model_name='scheduledtask',
            name='api_requests',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='API请求ID列表，用于API_REQUEST类型任务',
                verbose_name='API请求列表',
            ),
        ),
    ]
