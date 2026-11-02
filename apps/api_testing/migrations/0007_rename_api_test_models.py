import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0006_testsuitefolder'),
    ]

    operations = [
        migrations.RenameModel(old_name='TestSuite', new_name='ScenarioCase'),
        migrations.RenameModel(old_name='TestSet', new_name='TestSuite'),
        migrations.RenameModel(old_name='TestSuiteRequest', new_name='ScenarioCaseRequest'),
        migrations.RenameModel(old_name='TestSuiteFolder', new_name='ScenarioCaseFolder'),
        migrations.RenameModel(old_name='TestSetItem', new_name='TestSuiteItem'),
        migrations.RenameField(
            model_name='scenariocaserequest', old_name='test_suite', new_name='scenario_case',
        ),
        migrations.RenameField(
            model_name='testexecution', old_name='test_suite', new_name='scenario_case',
        ),
        migrations.RenameField(
            model_name='testsuite', old_name='suites', new_name='scenario_cases',
        ),
        migrations.RenameField(
            model_name='testsuiteitem', old_name='test_suite', new_name='scenario_case',
        ),
        migrations.RenameField(
            model_name='testsuiteitem', old_name='test_set', new_name='test_suite',
        ),
        migrations.RemoveField(model_name='testsuite', name='cron'),
        migrations.RemoveField(model_name='testsuite', name='enabled'),
        migrations.RemoveConstraint(
            model_name='scenariocasefolder', name='unique_test_suite_folder_path',
        ),
        migrations.AddConstraint(
            model_name='scenariocasefolder',
            constraint=models.UniqueConstraint(fields=('project', 'path'), name='unique_scenario_case_folder_path'),
        ),
        migrations.AlterUniqueTogether(
            name='scenariocaserequest', unique_together={('scenario_case', 'request')},
        ),
        migrations.AlterUniqueTogether(
            name='testsuiteitem', unique_together={('test_suite', 'scenario_case')},
        ),
        migrations.RemoveField(model_name='scheduledtask', name='test_suite'),
        migrations.AddField(
            model_name='scheduledtask',
            name='test_suite',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                to='api_testing.testsuite', verbose_name='测试套件',
            ),
        ),
        migrations.AlterModelTable(name='scenariocase', table='api_scenario_cases'),
        migrations.AlterModelTable(name='scenariocaserequest', table='api_scenario_case_requests'),
        migrations.AlterModelTable(name='scenariocasefolder', table='api_scenario_case_folders'),
        migrations.AlterModelTable(name='testsuite', table='api_test_suites'),
        migrations.AlterModelTable(name='testsuiteitem', table='api_test_suite_items'),
        migrations.AlterField(
            model_name='scenariocase', name='project',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='scenario_cases',
                to='api_testing.apiproject', verbose_name='所属项目',
            ),
        ),
        migrations.AlterField(
            model_name='scenariocase', name='created_by',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='scenario_cases',
                to='users.user', verbose_name='创建者',
            ),
        ),
        migrations.AlterField(
            model_name='scenariocase', name='description',
            field=models.TextField(blank=True, verbose_name='场景用例描述'),
        ),
        migrations.AlterField(
            model_name='scenariocase', name='name',
            field=models.CharField(max_length=200, verbose_name='场景用例名称'),
        ),
        migrations.AlterField(
            model_name='scenariocaserequest', name='scenario_case',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='api_testing.scenariocase',
                verbose_name='场景用例',
            ),
        ),
        migrations.AlterField(
            model_name='testexecution', name='scenario_case',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='executions',
                to='api_testing.scenariocase', verbose_name='场景用例',
            ),
        ),
        migrations.AlterField(
            model_name='testsuiteitem', name='test_suite',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to='api_testing.testsuite',
                verbose_name='测试套件',
            ),
        ),
        migrations.AlterField(
            model_name='scenariocasefolder', name='project',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='scenario_case_folders',
                to='api_testing.apiproject', verbose_name='所属项目',
            ),
        ),
        migrations.AlterField(
            model_name='testsuite', name='project',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='test_suites',
                to='api_testing.apiproject', verbose_name='所属项目',
            ),
        ),
        migrations.AlterField(
            model_name='testsuite', name='created_by',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='test_suites',
                to='users.user', verbose_name='创建者',
            ),
        ),
        migrations.AlterField(
            model_name='scenariocasefolder', name='path',
            field=models.CharField(max_length=200, verbose_name='文件夹路径'),
        ),
        migrations.AlterField(
            model_name='testsuite', name='name',
            field=models.CharField(max_length=200, verbose_name='测试套件名称'),
        ),
        migrations.AlterModelOptions(
            name='scenariocase',
            options={'ordering': ['-created_at'], 'verbose_name': '场景用例', 'verbose_name_plural': '场景用例'},
        ),
        migrations.AlterModelOptions(
            name='scenariocaserequest',
            options={'ordering': ['order'], 'verbose_name': '场景用例请求', 'verbose_name_plural': '场景用例请求'},
        ),
        migrations.AlterModelOptions(
            name='testsuite',
            options={'ordering': ['-created_at'], 'verbose_name': '测试套件', 'verbose_name_plural': '测试套件'},
        ),
        migrations.AlterModelOptions(
            name='testsuiteitem',
            options={'ordering': ['order'], 'verbose_name': '测试套件项', 'verbose_name_plural': '测试套件项'},
        ),
        migrations.AlterModelOptions(
            name='scenariocasefolder',
            options={'ordering': ['path'], 'verbose_name': '场景用例文件夹', 'verbose_name_plural': '场景用例文件夹'},
        ),
        migrations.AlterField(
            model_name='operationlog', name='resource_type',
            field=models.CharField(
                choices=[('project', '项目'), ('collection', '集合'), ('request', '请求'),
                         ('scenario_case', '场景用例'), ('environment', '环境'), ('task', '定时任务'),
                         ('execution', '执行记录')],
                max_length=20, verbose_name='资源类型',
            ),
        ),
        migrations.AlterField(
            model_name='scheduledtask', name='task_type',
            field=models.CharField(
                choices=[('TEST_SUITE', '测试套件执行'), ('API_REQUEST', 'API请求执行')],
                max_length=20, verbose_name='任务类型',
            ),
        ),
    ]
