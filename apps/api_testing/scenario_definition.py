import re
from collections import defaultdict
from copy import deepcopy
from uuid import uuid4

from .step_registry import StepValidationError, canonical_uuid, get_step_type


NODE_FIELDS = frozenset({
    'id', 'parent', 'position', 'type', 'name', 'enabled', 'config',
    'source_type', 'source_id',
})
STEP_REFERENCE_PATTERN = re.compile(r'(?<![\w])steps\.(?P<id>[A-Za-z0-9_\-]+)')


class ExternalStepReferenceError(StepValidationError):
    def __init__(self, references):
        self.references = tuple(sorted(references))
        super().__init__(f'复制范围外的步骤引用，必须先解除或一并复制：{", ".join(self.references)}')


def _source(node, capability):
    source_type = node.get('source_type')
    source_id = node.get('source_id')
    for field, value in (('source_type', source_type), ('source_id', source_id)):
        if value is not None and not isinstance(value, str):
            raise StepValidationError(f'{field} 必须是字符串或 null')
        if isinstance(value, str) and value and not value.strip():
            raise StepValidationError(f'{field} 不能是空白字符串')
    source_type = source_type or None
    source_id = source_id or None
    if (source_type is None) != (source_id is None):
        raise StepValidationError('source_type 和 source_id 必须成对赋值')
    if source_type is not None and source_type not in capability.allowed_source_types:
        raise StepValidationError(f'{capability.key} 不允许来源类型 {source_type!r}')
    if capability.source_required and source_type is None:
        raise StepValidationError(f'{capability.key} 必须指定 scenario_case 来源')
    return source_type, source_id


def _http_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise StepValidationError('HTTP name 必须是 1..255 字符的非空字符串')
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise StepValidationError('HTTP name 不允许控制字符')
    return value.strip()


def _node(value, previous):
    if not isinstance(value, dict):
        raise StepValidationError('步骤必须是对象')
    if any(not isinstance(key, str) for key in value):
        raise StepValidationError('步骤字段名必须是字符串')
    unknown = set(value) - NODE_FIELDS
    if unknown:
        raise StepValidationError(f'未知步骤字段：{", ".join(sorted(unknown))}')
    missing = {'id', 'position', 'type'} - set(value)
    if missing:
        raise StepValidationError(f'缺少步骤字段：{", ".join(sorted(missing))}')
    step_id = canonical_uuid(value['id'])
    capability = get_step_type(value['type'])
    old = previous.get(step_id)
    if old is not None and old['type'] != capability.key:
        raise StepValidationError(f'已有步骤 {step_id} 不能更换 type')
    parent = value.get('parent')
    if parent is not None:
        parent = canonical_uuid(parent, 'parent')
    position = value['position']
    if type(position) is not int or position < 0:
        raise StepValidationError('position 必须是非负整数，不能是布尔值')
    enabled = value.get('enabled', True)
    if type(enabled) is not bool:
        raise StepValidationError('enabled 必须是布尔值')
    if capability.name_editable:
        name = _http_name(value.get('name', old['name'] if old else capability.system_name))
    else:
        name = old['name'] if old else capability.system_name
        if old is not None and value.get('name', name) != name:
            raise StepValidationError(f'已有 {capability.key} 步骤的系统 name 不允许修改')
    source_type, source_id = _source(value, capability)
    return {
        'id': step_id,
        'parent': parent,
        'position': position,
        'type': capability.key,
        'name': name,
        'enabled': enabled,
        'config': capability.validate_config(value.get('config', {})),
        'source_type': source_type,
        'source_id': source_id,
    }


def _tree(nodes, max_depth):
    by_id = {node['id']: node for node in nodes}
    if len(by_id) != len(nodes):
        raise StepValidationError('步骤 id 重复')
    for node in nodes:
        parent = node['parent']
        if parent is not None and parent not in by_id:
            raise StepValidationError(f'parent {parent} 不在当前定义中，不允许跨场景 parent')
    depths = {}
    for step_id in by_id:
        path = []
        seen = set()
        current = step_id
        while current is not None and current not in depths:
            if current in seen:
                raise StepValidationError('步骤树存在自引用或环')
            seen.add(current)
            path.append(current)
            current = by_id[current]['parent']
        depth = depths.get(current, 0)
        for item in reversed(path):
            depth += 1
            if depth > max_depth:
                raise StepValidationError(f'步骤树深度超过 max_depth={max_depth}，根节点深度为 1')
            depths[item] = depth
    siblings = defaultdict(list)
    for node in nodes:
        parent = node['parent']
        if parent is not None and not get_step_type(by_id[parent]['type']).allows_children:
            raise StepValidationError(f'叶子步骤 {parent} 不允许子节点')
        siblings[parent].append(node)
    for children in siblings.values():
        children.sort(key=lambda node: (node['position'], node['id']))
        for position, node in enumerate(children):
            node['position'] = position
    paired = set()
    for children in siblings.values():
        for position, node in enumerate(children):
            if node['type'] != 'else':
                continue
            if_id = node['config']['if_step_id']
            condition = by_id.get(if_id)
            if condition is None or condition['type'] != 'condition':
                raise StepValidationError('Else 必须对应当前定义中的 If')
            if if_id in paired:
                raise StepValidationError('每个 If 只能配对唯一的 Else')
            paired.add(if_id)
            if condition['parent'] != node['parent']:
                raise StepValidationError('If/Else 必须具有相同 parent')
            if position == 0 or children[position - 1]['id'] != if_id:
                raise StepValidationError('Else 必须紧邻排列在对应 If 后面')
            if node['enabled'] != condition['enabled']:
                raise StepValidationError('If/Else 的 enabled 必须一致')
    ordered = []
    stack = list(reversed(siblings[None]))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(siblings.get(node['id'], [])))
    return ordered


def validate_definition(steps, *, previous_steps=None, source_resolver=None, max_depth=16):
    if not isinstance(steps, list):
        raise StepValidationError('steps 必须是扁平节点 list')
    if type(max_depth) is not int or max_depth < 1:
        raise StepValidationError('max_depth 必须是正整数，不能是布尔值')
    if source_resolver is not None and not callable(source_resolver):
        raise StepValidationError('source_resolver 必须可调用')
    previous = {}
    if previous_steps is not None:
        previous = {
            node['id']: node for node in validate_definition(previous_steps, max_depth=max_depth)
        }
    nodes = [_node(node, previous) for node in steps]
    ordered = _tree(nodes, max_depth)
    if source_resolver is not None:
        for node in ordered:
            if node['source_type'] is not None:
                target = source_resolver(node['source_type'], node['source_id'])
                if target is None or target is False:
                    raise StepValidationError(f'来源目标未通过服务校验：{node["source_type"]}/{node["source_id"]}')
    return ordered


def _rewrite_references(value, id_map, external):
    if isinstance(value, str):
        def replace(match):
            step_id = canonical_uuid(match.group('id'), 'steps 引用（必须使用稳定 UUID）')
            if step_id not in id_map:
                external.add(step_id)
                return match.group(0)
            return f'steps.{id_map[step_id]}'
        return STEP_REFERENCE_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_rewrite_references(item, id_map, external) for item in value]
    if isinstance(value, dict):
        return {
            _rewrite_references(key, id_map, external): _rewrite_references(item, id_map, external)
            for key, item in value.items()
        }
    return value


def _clone(nodes, source_resolver, max_depth):
    id_map = {node['id']: str(uuid4()) for node in nodes}
    clones = deepcopy(nodes)
    external = set()
    for node in clones:
        node['id'] = id_map[node['id']]
        node['parent'] = id_map.get(node['parent'])
        node['config'] = _rewrite_references(node['config'], id_map, external)
        if node['type'] == 'else':
            node['config']['if_step_id'] = id_map[node['config']['if_step_id']]
    if external:
        raise ExternalStepReferenceError(external)
    return validate_definition(clones, source_resolver=source_resolver, max_depth=max_depth)


def clone_definition(steps, *, source_resolver=None, max_depth=16):
    nodes = validate_definition(steps, max_depth=max_depth)
    return _clone(nodes, source_resolver, max_depth)


def clone_subtree(steps, step_id, *, source_resolver=None, max_depth=16):
    nodes = validate_definition(steps, max_depth=max_depth)
    by_id = {node['id']: node for node in nodes}
    step_id = canonical_uuid(step_id)
    if step_id not in by_id:
        raise StepValidationError(f'待复制步骤 {step_id} 不在当前定义中')
    roots = {step_id}
    selected = by_id[step_id]
    if selected['type'] == 'else':
        roots.add(selected['config']['if_step_id'])
    elif selected['type'] == 'condition':
        roots.update(node['id'] for node in nodes
                     if node['type'] == 'else' and node['config']['if_step_id'] == step_id)
    children = defaultdict(list)
    for node in nodes:
        children[node['parent']].append(node['id'])
    included = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        included.add(current)
        stack.extend(children.get(current, []))
    return _clone([node for node in nodes if node['id'] in included], source_resolver, max_depth)
