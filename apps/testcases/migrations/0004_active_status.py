from django.db import migrations, models


def activate_draft_cases(apps, schema_editor):
    """仅转换历史草稿，保留原有激活和废弃状态及更新时间。"""
    TestCase = apps.get_model('testcases', 'TestCase')
    TestCase.objects.using(schema_editor.connection.alias).filter(status='draft').update(status='active')


def add_status_constraint_if_missing(apps, schema_editor):
    """兼容已执行旧迁移的数据库，避免重复创建同名约束。"""
    TestCase = apps.get_model('testcases', 'TestCase')
    constraint = models.CheckConstraint(
        condition=models.Q(status__in=['active', 'deprecated']),
        name='testcase_status_valid',
    )
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, 'testcases')
    if constraint.name not in constraints:
        schema_editor.add_constraint(TestCase, constraint)


class Migration(migrations.Migration):

    dependencies = [
        ('testcases', '0003_structured_testcase_steps'),
    ]

    operations = [
        migrations.RunPython(activate_draft_cases, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='testcase',
            name='status',
            field=models.CharField(
                choices=[('active', '激活'), ('deprecated', '废弃')],
                default='active', max_length=20, verbose_name='状态',
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_status_constraint_if_missing, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name='testcase',
                    constraint=models.CheckConstraint(
                        condition=models.Q(status__in=['active', 'deprecated']),
                        name='testcase_status_valid',
                    ),
                ),
            ],
        ),
    ]
