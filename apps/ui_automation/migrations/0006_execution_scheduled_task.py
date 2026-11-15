"""保留用例、套件执行与定时任务的关联；历史记录维持空关联。"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('ui_automation', '0005_element_ui_elements_tree_idx')]

    operations = [
        migrations.AddField(
            model_name='testexecution', name='scheduled_task',
            field=models.ForeignKey(
                to='ui_automation.uischeduledtask', null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='suite_executions', verbose_name='定时任务'),
        ),
        migrations.AddField(
            model_name='testcaseexecution', name='scheduled_task',
            field=models.ForeignKey(
                to='ui_automation.uischeduledtask', null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='case_executions', verbose_name='定时任务'),
        ),
    ]
