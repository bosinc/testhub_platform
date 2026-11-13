import math
import re
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID


MAX_LOOP_ITERATIONS = 1000
MAX_WAIT_DURATION_MS = 300000
CONDITION_OPERATORS = frozenset({
    'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'not_contains',
    'exists', 'not_exists', 'is_empty', 'not_empty',
})
RIGHT_TYPES = frozenset({'string', 'number', 'boolean', 'null'})
ON_ERROR_POLICIES = frozenset({'continue', 'break_loop', 'abort_scenario'})
EXPRESSION_PATTERN = re.compile(
    r'\{\{\s*(?:env|vars|steps|loop)\.[A-Za-z0-9_.\-\[\]]+\s*\}\}'
)


class StepValidationError(ValueError):
    pass


def _object(value, allowed, required=()):
    if not isinstance(value, dict):
        raise StepValidationError('配置必须是对象')
    if any(not isinstance(key, str) for key in value):
        raise StepValidationError('配置字段名必须是字符串')
    unknown = set(value) - set(allowed)
    if unknown:
        raise StepValidationError(f'未知配置字段：{", ".join(sorted(unknown))}')
    missing = set(required) - set(value)
    if missing:
        raise StepValidationError(f'缺少配置字段：{", ".join(sorted(missing))}')


def _json_value(value, active=None, depth=0):
    if depth > 64:
        raise StepValidationError('配置 JSON 嵌套过深')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if not isinstance(value, (list, dict)):
        raise StepValidationError('配置必须是有限的 JSON 值')
    active = set() if active is None else active
    if id(value) in active:
        raise StepValidationError('配置 JSON 不允许循环引用')
    active.add(id(value))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise StepValidationError('配置 JSON 字段名必须是字符串')
        values = value.values()
    else:
        values = value
    for item in values:
        _json_value(item, active, depth + 1)
    active.remove(id(value))


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise StepValidationError(f'{field} 必须是非空字符串')
    return value


def _integer(value, field, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise StepValidationError(f'{field} 必须是 0..{maximum} 的整数，不能是布尔值')
    return value


def _expression(value, field):
    _text(value, field)
    if not EXPRESSION_PATTERN.fullmatch(value.strip()):
        raise StepValidationError(f'{field} 必须是完整的命名空间变量表达式')
    return value.strip()


def canonical_uuid(value, field='id'):
    if not isinstance(value, str):
        raise StepValidationError(f'{field} 必须是 UUID 字符串')
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise StepValidationError(f'{field} 必须是 UUID 字符串') from exc


def _condition(config):
    _object(config, {'left', 'operator', 'right', 'right_type'},
            {'left', 'operator', 'right', 'right_type'})
    left = config['left']
    if left is not None and type(left) not in (str, int, float, bool):
        raise StepValidationError('left 必须是 JSON 标量或变量表达式')
    operator = config['operator']
    right_type = config['right_type']
    if not isinstance(operator, str) or operator not in CONDITION_OPERATORS:
        raise StepValidationError('未知条件运算符')
    if not isinstance(right_type, str) or right_type not in RIGHT_TYPES:
        raise StepValidationError('未知 right_type')
    right = config['right']
    valid = {
        'string': isinstance(right, str),
        'number': type(right) in (int, float),
        'boolean': type(right) is bool,
        'null': right is None,
    }[right_type]
    if not valid:
        raise StepValidationError('right 与 right_type 不一致，不进行隐式类型转换')
    if operator in {'exists', 'not_exists', 'is_empty', 'not_empty'}:
        if right_type != 'null':
            raise StepValidationError('一元条件的 right 必须为 null，right_type 必须为 null')
    if operator in {'gt', 'gte', 'lt', 'lte'} and right_type != 'number':
        raise StepValidationError('大小比较的 right_type 必须是 number')
    if operator in {'contains', 'not_contains'} and right_type != 'string':
        raise StepValidationError('包含比较的 right_type 必须是 string')
    return deepcopy(config)


def _group(config):
    _object(config, set())
    return {}


def _else(config):
    _object(config, {'if_step_id'}, {'if_step_id'})
    return {'if_step_id': canonical_uuid(config['if_step_id'], 'if_step_id')}


def _loop_options(config):
    result = deepcopy(config)
    if 'break_if' in config and config['break_if'] is not None:
        result['break_if'] = _condition(config['break_if'])
    policy = config.get('on_error', 'abort_scenario')
    if not isinstance(policy, str) or policy not in ON_ERROR_POLICIES:
        raise StepValidationError('未知循环 on_error 策略')
    result['on_error'] = policy
    result['max_iterations'] = _integer(
        config.get('max_iterations', MAX_LOOP_ITERATIONS),
        'max_iterations', MAX_LOOP_ITERATIONS,
    )
    return result


def _for(config):
    _object(config, {'count', 'break_if', 'on_error', 'max_iterations'}, {'count'})
    result = _loop_options(config)
    count = config['count']
    if isinstance(count, str):
        result['count'] = _expression(count, 'count')
    else:
        result['count'] = _integer(count, 'count', result['max_iterations'])
    return result


def _foreach(config):
    _object(config, {'array_expression', 'break_if', 'on_error', 'max_iterations'},
            {'array_expression'})
    result = _loop_options(config)
    result['array_expression'] = _expression(config['array_expression'], 'array_expression')
    return result


def _wait(config):
    _object(config, {'duration_ms'}, {'duration_ms'})
    return {'duration_ms': _integer(config['duration_ms'], 'duration_ms', MAX_WAIT_DURATION_MS)}


def _script(config):
    _object(config, {'language', 'code'}, {'language', 'code'})
    if config['language'] != 'javascript':
        raise StepValidationError('脚本 language 仅支持 javascript；schema 不提供脚本运行时')
    _text(config['code'], 'code')
    return deepcopy(config)


def _source_meta(value):
    _object(value, {'name', 'imported_at', 'content_hash'})
    for field, item in value.items():
        _text(item, f'source_meta.{field}')
    return deepcopy(value)


def _http(config):
    from .request_dto import REQUEST_FIELDS, normalize_request

    _object(config, {'request', 'source_meta'}, {'request'})
    _object(config['request'], REQUEST_FIELDS)
    result = {'request': normalize_request(deepcopy(config['request']))}
    if result['request'].get('request_type', 'HTTP') != 'HTTP':
        raise StepValidationError('场景 HTTP 步骤不支持 WebSocket 请求编排')
    if 'source_meta' in config:
        result['source_meta'] = _source_meta(config['source_meta'])
    return result


def _mapping(value, field):
    if not isinstance(value, dict):
        raise StepValidationError(f'{field} 必须是对象映射')
    for key in value:
        _text(key, f'{field} 的变量名')
    return deepcopy(value)


def _scenario_ref(config):
    _object(config, {'input_mapping', 'output_mapping', 'source_meta'})
    result = {
        'input_mapping': _mapping(config.get('input_mapping', {}), 'input_mapping'),
        'output_mapping': _mapping(config.get('output_mapping', {}), 'output_mapping'),
    }
    if 'source_meta' in config:
        result['source_meta'] = _source_meta(config['source_meta'])
    return result


@dataclass(frozen=True)
class StepType:
    key: str
    system_name: str
    allows_children: bool
    name_editable: bool
    allowed_source_types: frozenset
    source_required: bool
    independently_creatable: bool
    config_validator: object

    def validate_config(self, config):
        _json_value(config)
        result = self.config_validator(config)
        _json_value(result)
        return deepcopy(result)


STEP_REGISTRY = MappingProxyType({
    step.key: step for step in (
        StepType('http', 'http', False, True, frozenset({'api_request'}), False, True, _http),
        StepType('group', 'group', True, False, frozenset(), False, True, _group),
        StepType('condition', 'condition', True, False, frozenset(), False, True, _condition),
        StepType('else', 'else', True, False, frozenset(), False, False, _else),
        StepType('for', 'for', True, False, frozenset(), False, True, _for),
        StepType('foreach', 'foreach', True, False, frozenset(), False, True, _foreach),
        StepType('wait', 'wait', False, False, frozenset(), False, True, _wait),
        StepType('script', 'script', False, False, frozenset(), False, True, _script),
        StepType('scenario_ref', 'scenario_ref', False, False,
                 frozenset({'scenario_case'}), True, True, _scenario_ref),
    )
})


def get_step_type(key):
    if not isinstance(key, str) or key not in STEP_REGISTRY:
        raise StepValidationError(f'未知步骤类型：{key!r}')
    return STEP_REGISTRY[key]
