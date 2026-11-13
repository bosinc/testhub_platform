"""Scenario definition persistence and controlled source resolution.

Only this module may resolve ``source_type + source_id`` into a real resource.
Views and the editor must go through it so a client cannot reach arbitrary
models or bypass project permissions.
"""

import hashlib
import json
from copy import deepcopy
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import ApiProject, ApiRequest, ScenarioCase, ScenarioStep
from .request_dto import request_from_model
from .scenario_definition import clone_definition, validate_definition
from .step_registry import STEP_REGISTRY, StepValidationError

MAX_REFERENCE_DEPTH = 8
EXECUTION_CONFIG_DEFAULTS = {'total_timeout_ms': 600000, 'on_error': 'stop'}
EXECUTION_ON_ERROR = ('stop', 'continue')


def _error(field, message):
    raise ValidationError({'errors': {field: [message]}})


def _can_access_project(user, project_id):
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return ApiProject.objects.filter(pk=project_id).filter(
        Q(owner=user) | Q(members=user)).exists()


def _as_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _content_hash(dto):
    payload = json.dumps(dto, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def resolve_source(scenario, source_type, source_id, *, user):
    """Return the source object or ``None`` when it is missing or not allowed."""
    identifier = _as_int(source_id)
    if identifier is None:
        return None
    if source_type == 'api_request':
        request = ApiRequest.objects.select_related('collection').filter(pk=identifier).first()
        if request is None:
            return None
        project_id = request.collection.project_id if request.collection_id else None
        if project_id != scenario.project_id or not _can_access_project(user, project_id):
            return None
        return request
    if source_type == 'scenario_case':
        target = ScenarioCase.objects.filter(pk=identifier).first()
        if target is None or target.pk == scenario.pk:
            return None
        if target.project_id != scenario.project_id or not _can_access_project(user, target.project_id):
            return None
        return target
    return None


def _source_resolver(scenario, user):
    def resolve(source_type, source_id):
        return resolve_source(scenario, source_type, source_id, user=user)

    return resolve


def _reference_targets(scenario, *, user):
    """Resolve existing scenario references; unresolvable targets are ignored here."""
    result = []
    source_ids = ScenarioStep.objects.filter(
        scenario_case=scenario, type='scenario_ref', source_type='scenario_case',
    ).values_list('source_id', flat=True)
    for source_id in source_ids:
        target = resolve_source(scenario, 'scenario_case', source_id, user=user)
        if target is not None:
            result.append(target)
    return result


def _reaches(start, goal_pk, *, user, depth=0, seen=None):
    """Return True when following scenario references from ``start`` reaches ``goal_pk``."""
    if start.pk == goal_pk:
        return True
    if depth > MAX_REFERENCE_DEPTH:
        return True
    seen = set() if seen is None else seen
    if start.pk in seen:
        return False
    seen.add(start.pk)
    for target in _reference_targets(start, user=user):
        if target.pk == goal_pk or _reaches(target, goal_pk, user=user, depth=depth + 1, seen=seen):
            return True
    return False


def _validate_references(scenario, nodes, *, user):
    for node in nodes:
        if node['type'] != 'scenario_ref':
            continue
        target = resolve_source(scenario, 'scenario_case', node['source_id'], user=user)
        if target is None:
            _error('steps', f'引用场景 {node["source_id"]} 不存在或无权限')
        if target.pk == scenario.pk:
            _error('steps', '场景不能引用自身')
        # Walk from the target; reaching this scenario means the new edge closes a cycle.
        if _reaches(target, scenario.pk, user=user):
            _error('steps', f'引用场景 {target.pk} 会形成循环引用')


def load_definition(scenario):
    """Return flat nodes in tree order so the editor can rebuild the tree."""
    steps = list(scenario.steps.all().order_by('position', 'id'))
    children = {}
    for step in steps:
        children.setdefault(step.parent_id, []).append(step)
    ordered = []
    stack = list(reversed(children.get(None, [])))
    while stack:
        step = stack.pop()
        ordered.append(step)
        stack.extend(reversed(children.get(step.id, [])))
    return [{
        'id': str(step.id),
        'parent': str(step.parent_id) if step.parent_id else None,
        'position': step.position,
        'type': step.type,
        'name': step.name,
        'enabled': step.enabled,
        'config': deepcopy(step.config),
        'source_type': step.source_type,
        'source_id': step.source_id,
    } for step in ordered]


def normalize_variables(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        _error('variables', '场景初始变量必须是对象')
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            _error('variables', '变量名必须是非空字符串')
        if isinstance(item, list):
            _error('variables', f'变量 {key} 必须是标量或对象')
        result[key] = deepcopy(item)
    return result


def normalize_execution_config(value):
    if value is None:
        return dict(EXECUTION_CONFIG_DEFAULTS)
    if not isinstance(value, dict):
        _error('execution_config', '执行配置必须是对象')
    unknown = value.keys() - EXECUTION_CONFIG_DEFAULTS.keys()
    if unknown:
        _error('execution_config', f'不支持的执行配置：{", ".join(sorted(unknown))}')
    config = {**EXECUTION_CONFIG_DEFAULTS, **deepcopy(value)}
    timeout = config['total_timeout_ms']
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1000 <= timeout <= 86400000:
        _error('execution_config', 'total_timeout_ms 必须是 1000..86400000 的整数')
    if config['on_error'] not in EXECUTION_ON_ERROR:
        _error('execution_config', 'on_error 必须是 stop 或 continue')
    return config


def validate_only(scenario, steps, *, user):
    """Validate a submitted definition without writing anything."""
    previous = load_definition(scenario)
    try:
        nodes = validate_definition(
            steps, previous_steps=previous, source_resolver=_source_resolver(scenario, user))
        _validate_references(scenario, nodes, user=user)
    except StepValidationError as exc:
        _error('steps', str(exc))
    return nodes


@transaction.atomic
def save_definition(scenario, steps, *, user, variables=None, execution_config=None):
    """Validate and persist the whole definition atomically.

    Concurrent saves are serialized by the scenario row lock; the last
    successful save wins, as the plan requires no version comparison.
    """
    scenario = ScenarioCase.objects.select_for_update().get(pk=scenario.pk)
    nodes = validate_only(scenario, steps, user=user)
    updates = []
    if variables is not None:
        scenario.variables = normalize_variables(variables)
        updates.append('variables')
    if execution_config is not None:
        scenario.execution_config = normalize_execution_config(execution_config)
        updates.append('execution_config')
    if updates:
        scenario.save(update_fields=updates)

    existing = {str(step.id): step for step in scenario.steps.all()}
    keep = []
    # Two passes: rows must exist before a parent foreign key can point at them.
    for node in nodes:
        keep.append(node['id'])
        step = existing.get(node['id'])
        fields = {
            'position': node['position'], 'type': node['type'], 'name': node['name'],
            'enabled': node['enabled'], 'config': node['config'],
            'source_type': node['source_type'], 'source_id': node['source_id'],
        }
        if step is None:
            ScenarioStep.objects.create(id=UUID(node['id']), scenario_case=scenario, **fields)
        else:
            for field, value in fields.items():
                setattr(step, field, value)
            step.save(update_fields=list(fields))
    scenario.steps.exclude(id__in=keep).delete()
    for node in nodes:
        parent = node['parent']
        ScenarioStep.objects.filter(pk=node['id']).update(
            parent_id=parent if parent else None)
    return load_definition(scenario)


def _sibling_position(scenario, parent_id):
    siblings = scenario.steps.filter(parent_id=parent_id)
    return siblings.count()


def _append_nodes(scenario, nodes, *, parent_id, position=None):
    """Insert cloned nodes after an existing parent, offsetting sibling positions."""
    current = load_definition(scenario)
    parent_id = str(parent_id) if parent_id else None
    start = _sibling_position(scenario, parent_id) if position is None else position
    for node in nodes:
        if node['parent'] is None:
            node['parent'] = parent_id
            node['position'] = start
            start += 1
    return current + nodes


def import_api_request(scenario, request_id, *, user, parent_id=None):
    """Create an HTTP step from a saved request; the request stays a source only."""
    identifier = _as_int(request_id)
    request = ApiRequest.objects.select_related('collection').filter(pk=identifier).first() if identifier else None
    if request is None:
        _error('request_id', '单接口用例不存在')
    project_id = request.collection.project_id if request.collection_id else None
    if project_id != scenario.project_id or not _can_access_project(user, project_id):
        raise PermissionDenied('没有该单接口用例的访问权限')
    dto = request_from_model(request)
    node = {
        'id': str(uuid4()), 'parent': None, 'position': 0, 'type': 'http',
        'name': request.name[:255], 'enabled': True,
        'config': {'request': dto, 'source_meta': {'content_hash': _content_hash(dto)}},
        'source_type': 'api_request', 'source_id': str(request.pk),
    }
    nodes = _append_nodes(scenario, [node], parent_id=parent_id)
    return save_definition(scenario, nodes, user=user)


def import_scenario_steps(scenario, source_id, *, user, parent_id=None):
    """Deep-copy another scenario's steps into this one, rewriting step ids."""
    target = resolve_source(scenario, 'scenario_case', source_id, user=user)
    if target is None:
        _error('scenario_id', '引用场景不存在或无权限')
    if target.pk == scenario.pk or _reaches(target, scenario.pk, user=user):
        _error('scenario_id', '不能导入自身或形成循环引用')
    clones = clone_definition(load_definition(target))
    nodes = _append_nodes(scenario, clones, parent_id=parent_id)
    return save_definition(scenario, nodes, user=user)


def source_snapshot(step, *, user):
    """Return the current source request DTO, or ``None`` when the source is gone."""
    if step.type != 'http' or step.source_type != 'api_request':
        return None
    request = resolve_source(step.scenario_case, 'api_request', step.source_id, user=user)
    if request is None:
        return None
    dto = request_from_model(request)
    return {'request': dto, 'content_hash': _content_hash(dto)}


def source_changed(step, *, user):
    snapshot = source_snapshot(step, user=user)
    if snapshot is None:
        return False
    stored = (step.config or {}).get('source_meta', {}).get('content_hash')
    return stored != snapshot['content_hash']


def sync_from_source(step, *, user, overwrite_name=False):
    """Copy the source request into the local step; only an explicit call does this."""
    snapshot = source_snapshot(step, user=user)
    if snapshot is None:
        raise NotFound('来源单接口用例已失效')
    step.config = {**(step.config or {}), 'request': snapshot['request'],
                   'source_meta': {'content_hash': snapshot['content_hash']}}
    fields = ['config']
    if overwrite_name and step.type == 'http':
        request = resolve_source(step.scenario_case, 'api_request', step.source_id, user=user)
        step.name = request.name[:255]
        fields.append('name')
    step.save(update_fields=fields)
    return step


def delete_scenario_with_references(scenario, *, user):
    """Block deletion while another scenario still references this one."""
    for candidate in ScenarioCase.objects.filter(project=scenario.project).exclude(pk=scenario.pk):
        if candidate.steps.filter(source_type='scenario_case', source_id=str(scenario.pk)).exists():
            _error('scenario_id', f'场景仍被 {candidate.name} 引用，请先解除引用')
    scenario.delete()


def step_capabilities():
    """Expose the registry so the editor can render types without hardcoding."""
    return {
        key: {
            'name': step.system_name,
            'allows_children': step.allows_children,
            'name_editable': step.name_editable,
            'allowed_source_types': sorted(step.allowed_source_types),
            'independently_creatable': step.independently_creatable,
        }
        for key, step in STEP_REGISTRY.items()
    }
