"""Convert legacy scenario request links before removing their old schema.

The conversion is deliberately kept self-contained so this migration remains
usable even if the runtime request DTO changes in a later release.
"""

from copy import deepcopy
import hashlib
import json

from django.db import migrations, models


def _request_snapshot(request, assertions, extractors):
    """Build the detached request configuration expected by HTTP steps."""
    snapshot = {
        'method': request.method,
        'url': request.url,
        'request_type': request.request_type,
        'headers': deepcopy(request.headers),
        'params': deepcopy(request.params),
        'path_params': deepcopy(request.path_params),
        'body': deepcopy(request.body),
        'auth': deepcopy(request.auth),
        'scripts': {
            'pre_request': request.pre_request_script,
            'post_request': request.post_request_script,
        },
        # Scenario-level rules must win over the source request defaults.
        'assertions': deepcopy(assertions),
        'extractors': deepcopy(extractors),
        'settings': deepcopy(request.settings),
    }
    try:
        json.dumps(snapshot, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f'Cannot serialize request {request.pk} for scenario migration'
        ) from exc
    return snapshot


def _content_hash(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def migrate_legacy_scenario_case_requests(apps, schema_editor):
    """Create one root HTTP step for every legacy scenario request link."""
    ScenarioCaseRequest = apps.get_model('api_testing', 'ScenarioCaseRequest')
    ScenarioStep = apps.get_model('api_testing', 'ScenarioStep')
    alias = schema_editor.connection.alias

    links = ScenarioCaseRequest.objects.using(alias).select_related(
        'scenario_case', 'request',
    ).order_by('scenario_case_id', 'order', 'pk')
    link_scenario_ids = set(
        links.values_list('scenario_case_id', flat=True).distinct()
    )
    existing_scenarios = set(
        ScenarioStep.objects.using(alias).filter(
            scenario_case_id__in=link_scenario_ids,
        ).values_list('scenario_case_id', flat=True).distinct()
    )
    # 场景树和旧请求关系可能在同一数据库中并存：用户已在新编辑器中
    # 保存过的场景以 ScenarioStep 为准，不能再次把旧请求复制成重复步骤，
    # 也不能因为其中一个场景已迁移而阻塞其他场景的迁移。
    if existing_scenarios:
        links = links.exclude(scenario_case_id__in=existing_scenarios)

    # Validate every source row before creating any replacement rows. This keeps
    # a malformed later row from leaving a partially converted scenario behind.
    converted_steps = []
    next_reference_ids = {}
    for link in links.iterator(chunk_size=500):
        scenario_id = link.scenario_case_id
        request = link.request
        if request.request_type != 'HTTP':
            raise ValueError(
                f'Cannot migrate non-HTTP request {request.pk} '
                f'from scenario {scenario_id}'
            )

        request_data = _request_snapshot(
            request,
            link.assertions,
            link.extractors,
        )
        reference_id = next_reference_ids.get(scenario_id, 1)
        next_reference_ids[scenario_id] = reference_id + 1
        converted_steps.append(ScenarioStep(
            scenario_case_id=scenario_id,
            reference_id=reference_id,
            parent_id=None,
            position=reference_id - 1,
            type='http',
            name=request.name[:255],
            enabled=link.enabled,
            config={
                'request': request_data,
                'source_meta': {
                    'content_hash': _content_hash(request_data),
                },
            },
            source_type='api_request',
            source_id=str(request.pk),
        ))

    ScenarioStep.objects.using(alias).bulk_create(converted_steps, batch_size=500)


def reverse_legacy_scenario_case_requests(apps, schema_editor):
    """Prevent a rollback that would silently discard converted steps."""
    raise RuntimeError(
        'This migration is irreversible after legacy scenario request data '
        'has been converted and deleted; restore the database backup instead.'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('api_testing', '0005_step_execution_records'),
    ]

    operations = [
        migrations.RunPython(
            migrate_legacy_scenario_case_requests,
            reverse_legacy_scenario_case_requests,
        ),
        migrations.RemoveField(
            model_name='scenariocase',
            name='requests',
        ),
        migrations.DeleteModel(
            name='ScenarioCaseRequest',
        ),
        migrations.AddField(
            model_name='stepexecution',
            name='output',
            field=models.JSONField(blank=True, default=dict, verbose_name='步骤输出'),
        ),
    ]
