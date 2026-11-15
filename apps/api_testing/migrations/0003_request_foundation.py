"""Stage 1 schema migration, not the final request/scenario execution cutover.

Only unambiguous structural data is converted. URL, environment and variable
mapping needs a separate preflight before deployment of the final execution
chain. Existing scenario tables and relationships remain intact at this stage.
"""

from copy import deepcopy
import re
from urllib.parse import urlsplit

from django.db import migrations, models


PATH_PARAMETER_RE = re.compile(r'(?<!\{)\{([A-Za-z_][\w.-]*)\}(?!\})')


def ordered_entries(value):
    if isinstance(value, list):
        return deepcopy(value)
    if not isinstance(value, dict):
        raise ValueError('Expected a legacy object or an ordered entry list')
    entries = []
    for key, item in value.items():
        if isinstance(item, dict) and 'value' in item:
            entry = {'key': key, **deepcopy(item)}
        else:
            entry = {'key': key, 'value': deepcopy(item)}
        entry.setdefault('enabled', True)
        entry.setdefault('type', 'json' if isinstance(entry['value'], (dict, list)) else 'string')
        entry.setdefault('description', '')
        entries.append(entry)
    return entries


def convert_request(request):
    """Use only row-local evidence and retain disabled/empty values verbatim."""
    headers = ordered_entries(request.headers)
    params = ordered_entries(request.params)
    path_params = ordered_entries(request.path_params)
    try:
        path = urlsplit(request.url).path
    except ValueError:
        path = ''
    placeholders = set(PATH_PARAMETER_RE.findall(path))
    query_params = []
    for entry in params:
        if isinstance(entry, dict) and entry.get('key') in placeholders:
            path_params.append(deepcopy(entry))
        else:
            query_params.append(entry)
    body = deepcopy(request.body)
    if isinstance(body, dict):
        kind = body.get('type')
        aliases = {'formdata': 'form-data', 'urlencoded': 'x-www-form-urlencoded'}
        kind = aliases.get(kind, kind) if isinstance(kind, str) else kind
        if kind in ('form-data', 'x-www-form-urlencoded'):
            body['type'] = kind
            body['data'] = ordered_entries(body.get('data', {}))
    return {'headers': headers, 'params': query_params, 'path_params': path_params, 'body': body}


def migrate_request_entries(apps, schema_editor):
    Request = apps.get_model('api_testing', 'ApiRequest')
    alias = schema_editor.connection.alias
    for request in Request.objects.using(alias).all().iterator(chunk_size=500):
        try:
            converted = convert_request(request)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Cannot convert ApiRequest {request.pk}: {exc}') from exc
        for field, value in converted.items():
            setattr(request, field, value)
        request.save(using=alias, update_fields=list(converted))


class Migration(migrations.Migration):
    dependencies = [('api_testing', '0002_initial')]

    operations = [
        migrations.AddField(
            model_name='environment', name='base_url',
            field=models.TextField(blank=True, default='', verbose_name='前置URL'),
        ),
        migrations.AddField(
            model_name='apirequest', name='path_params',
            field=models.JSONField(default=list, verbose_name='路径参数'),
        ),
        migrations.AddField(
            model_name='apirequest', name='settings',
            field=models.JSONField(default=dict, verbose_name='请求设置'),
        ),
        migrations.AlterField(
            model_name='apirequest', name='headers',
            field=models.JSONField(default=list, verbose_name='请求头'),
        ),
        migrations.AlterField(
            model_name='apirequest', name='params',
            field=models.JSONField(default=list, verbose_name='URL参数'),
        ),
        # Structural conversion is intentionally irreversible; restore backups
        # for rollback rather than collapsing ordered entries into lossy dicts.
        migrations.RunPython(migrate_request_entries),
    ]
