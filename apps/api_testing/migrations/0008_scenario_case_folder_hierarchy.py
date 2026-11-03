from django.db import migrations, models
import django.db.models.deletion


def migrate_folder_paths(apps, schema_editor):
    """将旧路径拆分为父子文件夹，并把场景用例绑定到末级文件夹。"""
    Folder = apps.get_model('api_testing', 'ScenarioCaseFolder')
    ScenarioCase = apps.get_model('api_testing', 'ScenarioCase')

    folder_map = {}
    legacy_folders = list(Folder.objects.all().order_by('project_id', 'path'))
    legacy_by_path = {(folder.project_id, folder.path): folder for folder in legacy_folders}
    for legacy_folder in legacy_folders:
        parent = None
        parts = [part for part in legacy_folder.path.split('/') if part]
        current_path = []
        for index, part in enumerate(parts):
            current_path.append(part)
            key = (legacy_folder.project_id, '/'.join(current_path))
            if key in folder_map:
                parent = folder_map[key]
                continue
            candidate = legacy_by_path.get(key)
            if candidate:
                candidate.name = part
                candidate.parent_id = parent.pk if parent else None
                candidate.save(update_fields=['name', 'parent'])
                parent = candidate
            else:
                parent = Folder.objects.create(
                    project_id=legacy_folder.project_id,
                    parent_id=parent.pk if parent else None,
                    name=part,
                )
            folder_map[key] = parent

    for scenario_case in ScenarioCase.objects.all().iterator():
        if not scenario_case.legacy_folder:
            continue
        folder = folder_map.get((scenario_case.project_id, scenario_case.legacy_folder))
        if folder:
            scenario_case.folder_id = folder.pk
            scenario_case.save(update_fields=['folder'])


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0007_rename_api_test_models'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='scenariocasefolder', name='unique_scenario_case_folder_path',
        ),
        migrations.AddField(
            model_name='scenariocasefolder', name='name',
            field=models.CharField(max_length=200, null=True, verbose_name='文件夹名称'),
        ),
        migrations.AddField(
            model_name='scenariocasefolder', name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='children', to='api_testing.scenariocasefolder',
                                    verbose_name='父文件夹'),
        ),
        migrations.RenameField(
            model_name='scenariocase', old_name='folder', new_name='legacy_folder',
        ),
        migrations.AddField(
            model_name='scenariocase', name='folder',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='scenario_cases', to='api_testing.scenariocasefolder',
                                    verbose_name='所属文件夹'),
        ),
        migrations.RunPython(migrate_folder_paths, migrations.RunPython.noop),
        migrations.RemoveField(model_name='scenariocasefolder', name='path'),
        migrations.RemoveField(model_name='scenariocase', name='legacy_folder'),
        migrations.AlterField(
            model_name='scenariocasefolder', name='name',
            field=models.CharField(max_length=200, verbose_name='文件夹名称'),
        ),
        migrations.AddConstraint(
            model_name='scenariocasefolder',
            constraint=models.UniqueConstraint(fields=('project', 'parent', 'name'),
                                               name='unique_scenario_case_folder_name'),
        ),
        migrations.AlterModelOptions(
            name='scenariocasefolder',
            options={'ordering': ['name'], 'verbose_name': '场景用例文件夹', 'verbose_name_plural': '场景用例文件夹'},
        ),
    ]
