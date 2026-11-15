"""Strict request DTO normalization without variable resolution or network I/O."""

import base64
import binascii
from copy import deepcopy
import json
import math

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from .environment_resolver import validate_request_path


REQUEST_FIELDS = (
    'method', 'url', 'request_type', 'headers', 'params', 'path_params',
    'body', 'auth', 'scripts', 'assertions', 'extractors', 'settings',
)
DEFAULT_SETTINGS = {
    'timeout': 30, 'connect_timeout': 5, 'verify_ssl': True, 'follow_redirects': True,
}
DEFAULT_REQUEST = {
    'method': 'GET', 'url': '', 'request_type': 'HTTP', 'headers': [], 'params': [],
    'path_params': [], 'body': {'type': 'none'}, 'auth': {'type': 'none'},
    'scripts': {'pre_request': '', 'post_request': ''}, 'assertions': [],
    'extractors': [], 'settings': DEFAULT_SETTINGS,
}
ENTRY_TYPES = {'string', 'text', 'number', 'integer', 'boolean', 'json'}


def _error(field, message):
    raise ValidationError({'errors': {field: [message]}})


def _object(value, field, allowed):
    if not isinstance(value, dict):
        _error(field, _('必须是对象'))
    unknown = value.keys() - allowed
    if unknown:
        _error(field, _('不支持的字段: %(fields)s') % {'fields': ', '.join(sorted(map(str, unknown)))})


def _json_value(value, field):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        _error(field, _('必须是有效的JSON值'))


def normalize_file(value, field):
    """Files contain bytes, never a local file-system path or an arbitrary URL."""
    fields = {'filename', 'content_base64', 'content_type'}
    _object(value, field, fields)
    if value.keys() != fields or not all(isinstance(value[key], str) for key in fields):
        _error(field, _('文件必须包含filename、content_base64、content_type字符串'))
    filename = value['filename']
    if (not filename or filename in ('.', '..') or '/' in filename or '\\' in filename
            or ':' in filename or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
        _error(field, _('filename必须是文件名，不能是本机路径'))
    if not value['content_type'] or any(ord(char) < 32 or ord(char) == 127 for char in value['content_type']):
        _error(field, _('content_type不能为空或包含控制字符'))
    try:
        base64.b64decode(value['content_base64'], validate=True)
    except (ValueError, binascii.Error):
        _error(field, _('content_base64必须是有效的Base64内容'))
    return deepcopy(value)


def normalize_entries(value, field, *, allow_files=False):
    """Keep order, duplicates and disabled rows; path keys are always unique."""
    if not isinstance(value, list):
        _error(field, _('必须使用有序条目列表，不接受字典'))
    result = []
    seen = set()
    for index, source in enumerate(value):
        location = f'{field}.{index}'
        _object(source, location, {'key', 'value', 'enabled', 'type', 'description'})
        entry = {'key': '', 'value': '', 'enabled': True, 'type': 'string', 'description': ''}
        entry.update(deepcopy(source))
        if not isinstance(entry['key'], str) or entry['enabled'] and not entry['key']:
            _error(location, _('启用条目的key必须是非空字符串'))
        if type(entry['enabled']) is not bool:
            _error(location, _('enabled必须是布尔值'))
        if not isinstance(entry['description'], str):
            _error(location, _('description必须是字符串'))
        allowed_types = ENTRY_TYPES | ({'file'} if allow_files else set())
        if not isinstance(entry['type'], str) or entry['type'] not in allowed_types:
            _error(location, _('不支持的条目类型'))
        data = entry['value']
        if entry['type'] == 'file':
            entry['value'] = normalize_file(data, location + '.value')
        elif entry['type'] in ('number', 'integer') and isinstance(data, bool):
            _error(location, _('布尔值不能用作数字'))
        elif entry['type'] != 'json' and isinstance(data, (dict, list)):
            _error(location, _('结构化条目值必须使用json类型'))
        _json_value(entry['value'], location + '.value')
        if field == 'path_params':
            if not entry['key'] or entry['key'] in seen:
                _error(location, _('路径参数key不能为空或重复，包括禁用条目'))
            seen.add(entry['key'])
            if entry['enabled'] and ('value' not in source or data is None or data == ''):
                _error(location, _('启用路径参数必须提供非空值'))
            if isinstance(data, (dict, list)):
                _error(location, _('路径参数值必须是标量'))
        result.append(entry)
    return result


def _body(value):
    _object(value, 'body', {'type', 'data'})
    kind = value.get('type', 'none')
    if not isinstance(kind, str) or kind not in (
            'none', 'json', 'raw', 'form-data', 'x-www-form-urlencoded', 'binary'):
        _error('body.type', _('不支持的请求体类型'))
    if kind == 'none':
        if 'data' in value:
            _error('body', _('none请求体不能包含data'))
        return {'type': 'none'}
    if kind in ('form-data', 'x-www-form-urlencoded'):
        data = normalize_entries(value.get('data', []), 'body.data', allow_files=kind == 'form-data')
    elif kind == 'binary':
        data = normalize_file(value.get('data'), 'body.data')
    elif kind == 'raw':
        data = value.get('data', '')
        if not isinstance(data, str):
            _error('body.data', _('raw请求体必须是字符串'))
    else:
        data = deepcopy(value.get('data', {}))
        _json_value(data, 'body.data')
    return {'type': kind, 'data': data}


def _auth(value):
    if not isinstance(value, dict):
        _error('auth', _('认证配置必须是对象'))
    kind = value.get('type', 'none')
    defaults = {
        'none': {}, 'basic': {'username': '', 'password': ''},
        'bearer': {'token': ''}, 'api_key': {'key': '', 'value': '', 'in': 'header'},
    }
    if not isinstance(kind, str) or kind not in defaults:
        _error('auth.type', _('不支持的认证类型'))
    _object(value, 'auth', {'type'} | defaults[kind].keys())
    result = {'type': kind, **defaults[kind], **deepcopy(value)}
    if not all(isinstance(item, str) for item in result.values()):
        _error('auth', _('认证字段必须是字符串'))
    if kind == 'api_key' and result['in'] not in ('header', 'query'):
        _error('auth.in', _('API Key只能放在header或query'))
    return result


def _settings(value):
    _object(value, 'settings', DEFAULT_SETTINGS.keys())
    settings = {**DEFAULT_SETTINGS, **deepcopy(value)}
    for field, maximum in (('timeout', 300), ('connect_timeout', 60)):
        number = settings[field]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not 0 < number <= maximum or not math.isfinite(number)):
            _error(f'settings.{field}', _('超时必须为大于0且不超过%(maximum)s秒的数字') % {'maximum': maximum})
    for field in ('verify_ssl', 'follow_redirects'):
        if type(settings[field]) is not bool:
            _error(f'settings.{field}', _('必须是布尔值'))
    return settings


def normalize_request(data):
    """Return a complete, detached DTO; old dict-based entries are rejected."""
    _object(data, 'request', set(REQUEST_FIELDS))
    request = deepcopy(DEFAULT_REQUEST)
    request.update(deepcopy(data))
    if request['method'] not in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'):
        _error('method', _('不支持的请求方法'))
    if request['request_type'] not in ('HTTP', 'WEBSOCKET'):
        _error('request_type', _('不支持的请求类型'))
    validate_request_path(request['url'], allow_variables=True)
    for field in ('headers', 'params', 'path_params'):
        request[field] = normalize_entries(request[field], field)
    request['body'] = _body(request['body'])
    request['auth'] = _auth(request['auth'])
    _object(request['scripts'], 'scripts', {'pre_request', 'post_request'})
    request['scripts'] = {**DEFAULT_REQUEST['scripts'], **request['scripts']}
    if not all(isinstance(script, str) for script in request['scripts'].values()):
        _error('scripts', _('前后置脚本必须是字符串'))
    for field in ('assertions', 'extractors'):
        if not isinstance(request[field], list):
            _error(field, _('必须是规则对象列表'))
        for index, rule in enumerate(request[field]):
            if not isinstance(rule, dict):
                _error(f'{field}.{index}', _('规则必须是对象'))
            if 'enabled' in rule and type(rule['enabled']) is not bool:
                _error(f'{field}.{index}.enabled', _('必须是布尔值'))
            _json_value(rule, f'{field}.{index}')
    request['settings'] = _settings(request['settings'])
    return request


def request_from_model(obj, overrides=None):
    """Project model fields and the existing script columns into the DTO."""
    data = {field: deepcopy(getattr(obj, field)) for field in REQUEST_FIELDS if field != 'scripts'}
    data['scripts'] = {'pre_request': obj.pre_request_script, 'post_request': obj.post_request_script}
    if overrides is not None:
        _object(overrides, 'overrides', set(REQUEST_FIELDS))
        data.update(deepcopy(overrides))
    return normalize_request(data)
