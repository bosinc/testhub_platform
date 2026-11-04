# UI 用例目录直接使用父子关系保存，不持久化完整目录路径。

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ui_automation', '0011_scriptstep_storage_state'),
    ]

    operations = [
        migrations.CreateModel(
            name='TestCaseFolder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='文件夹名称')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='ui_automation.testcasefolder', verbose_name='父文件夹')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='test_case_folders', to='ui_automation.uiproject', verbose_name='所属项目')),
            ],
            options={
                'verbose_name': 'UI 测试用例文件夹',
                'verbose_name_plural': 'UI 测试用例文件夹',
                'db_table': 'ui_test_case_folders',
                'ordering': ['name'],
                'constraints': [models.UniqueConstraint(fields=('project', 'parent', 'name'), name='unique_ui_test_case_folder_name')],
            },
        ),
        migrations.AddField(
            model_name='testcase',
            name='folder',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='test_cases', to='ui_automation.testcasefolder', verbose_name='所属文件夹'),
        ),
    ]
