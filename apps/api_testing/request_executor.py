"""Shared request executor for interface debugging and (later) scenario steps.

The executor only consumes the canonical request DTO, so every entry point sends
the same transport representation instead of translating dictionaries locally.
"""

import base64
import json
import time
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from rest_framework.exceptions import ValidationError

from .environment_resolver import build_request_url
from .request_dto import normalize_request
from .scenario_variables import VariableResolutionError, VariableScope, _as_text
from .utils import _api_request_retry
from .variable_resolver import VariableResolver


LITERAL_HINT = '；如需按原文发送，请在花括号前加反斜杠，例如 \\{{ name }}'


def resolve_request(dto, variables):
    """Resolve ``{{...}}`` placeholders exactly as a scenario step does.

    ``variables`` is a ``VariableScope``, or a plain mapping that is read as the
    environment variables. An unknown variable stops the request.
    """
    scope = variables if isinstance(variables, VariableScope) else VariableScope(
        environment=variables)
    # Scripts are stored source code, not request data: their braces are not variables.
    template = {key: value for key, value in dto.items() if key != 'scripts'}
    try:
        resolved = scope.resolve_value(template)
    except VariableResolutionError as exc:
        raise ValidationError({'errors': {'variables': [f'{exc}{LITERAL_HINT}']}})
    if 'scripts' in dto:
        resolved['scripts'] = dto['scripts']
    # A whole-value placeholder keeps its JSON type, but credentials are always text.
    if isinstance(resolved.get('auth'), dict):
        resolved['auth'] = {key: value if isinstance(value, str) else _as_text(value)
                            for key, value in resolved['auth'].items()}
    return resolved


def _resolved(value, resolver):
    """Evaluate ``${...}`` dynamic functions; ``{{...}}`` is resolved beforehand."""
    if isinstance(value, str):
        return resolver.resolve(value)
    if isinstance(value, list):
        return [_resolved(item, resolver) for item in value]
    if isinstance(value, dict):
        return {key: _resolved(item, resolver) for key, item in value.items()}
    return value


def _enabled(entries):
    return [entry for entry in entries or [] if entry.get('enabled', True)]


def _decode_file(value):
    try:
        return base64.b64decode(value['content_base64'], validate=True)
    except (KeyError, ValueError):
        raise ValidationError({'errors': {'body.data': ['文件内容不是有效的Base64']}})


def _entry_value(entry, resolver):
    value = entry.get('value')
    kind = entry.get('type', 'string')
    if kind == 'file':
        return _decode_file(value)
    value = _resolved(value, resolver)
    if kind == 'json' and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            raise ValidationError({'errors': {f'entry.{entry.get("key")}': ['JSON 条目内容无效']}})
    if kind in ('number', 'integer'):
        try:
            return float(value) if kind == 'number' else int(value)
        except (TypeError, ValueError):
            raise ValidationError({'errors': {f'entry.{entry.get("key")}': ['数字条目内容无效']}})
    if kind == 'boolean':
        if isinstance(value, bool):
            return value
        if str(value).lower() in ('true', 'false'):
            return str(value).lower() == 'true'
        raise ValidationError({'errors': {f'entry.{entry.get("key")}': ['布尔条目内容无效']}})
    return value


def build_transport(dto, *, base_url, variables=None, resolver=None):
    """Translate a normalized DTO into requests arguments plus the final URL.

    Pass ``variables`` for a request template (see ``resolve_request``). Omit it
    for a DTO that is already resolved, whose text must be sent as it is.
    """
    resolver = resolver or VariableResolver()
    if variables is not None:
        dto = resolve_request(dto, variables)
    request = normalize_request(dto)
    # The address takes variables like every other part of the request.
    path_params = [{**entry, 'value': _resolved(entry['value'], resolver)}
                   for entry in _enabled(request['path_params'])]
    url = build_request_url(
        base_url, _resolved(request['url'], resolver), path_params,
        request_type=request['request_type'],
    )

    headers = {}
    for entry in _enabled(request['headers']):
        headers[entry['key']] = str(_entry_value(entry, resolver))

    query = []
    for entry in _enabled(request['params']):
        value = _entry_value(entry, resolver)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        query.append((entry['key'], value))

    auth = request['auth']
    auth_value = None
    auth_type = auth.get('type', 'none')
    if auth_type == 'basic':
        auth_value = (_resolved(auth.get('username', ''), resolver),
                      _resolved(auth.get('password', ''), resolver))
    elif auth_type == 'bearer':
        headers['Authorization'] = f"Bearer {_resolved(auth.get('token', ''), resolver)}"
    elif auth_type == 'api_key' and auth.get('key'):
        key = _resolved(auth['key'], resolver)
        value = _resolved(auth.get('value', ''), resolver)
        if auth.get('in') == 'query':
            query.append((key, value))
        else:
            headers[key] = value

    body = request['body']
    kwargs = {'data': None, 'json': None, 'files': None}
    kind = body.get('type', 'none')
    if kind == 'json':
        kwargs['json'] = _resolved(body.get('data'), resolver)
    elif kind == 'raw':
        kwargs['data'] = _resolved(body.get('data', ''), resolver)
        if not any(key.lower() == 'content-type' for key in headers):
            headers['Content-Type'] = 'text/plain; charset=utf-8'
    elif kind == 'x-www-form-urlencoded':
        kwargs['data'] = urlencode([
            (entry['key'], _entry_value(entry, resolver))
            for entry in _enabled(body.get('data'))
        ])
        headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
    elif kind == 'form-data':
        data, files = {}, {}
        for entry in _enabled(body.get('data')):
            value = _entry_value(entry, resolver)
            if entry.get('type') == 'file':
                files[entry['key']] = (entry['value']['filename'], value,
                                       entry['value'].get('content_type'))
            else:
                data[entry['key']] = value
        kwargs['data'], kwargs['files'] = data, files or None
    elif kind == 'binary' and body.get('data'):
        kwargs['data'] = _decode_file(body['data'])
        headers.setdefault('Content-Type', body['data'].get('content_type') or 'application/octet-stream')

    settings = request['settings']
    kwargs.update(
        method=request['method'], url=url, headers=headers, params=query,
        timeout=(settings['connect_timeout'], settings['timeout']),
        verify=settings['verify_ssl'], allow_redirects=settings['follow_redirects'],
        auth=auth_value,
    )
    return kwargs


def execute_request(dto, *, base_url, variables=None, session=None):
    """Send one request and return the raw response with timing metadata."""
    kwargs = build_transport(dto, base_url=base_url, variables=variables)
    method = kwargs['method']
    owns_session = session is None
    session = session or requests.Session()
    adapter = HTTPAdapter(max_retries=_api_request_retry(method))
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    started = time.perf_counter()
    try:
        response = session.request(**kwargs)
    finally:
        if owns_session:
            session.close()
    return {
        'response': response,
        'response_time': (time.perf_counter() - started) * 1000,
        'request': {
            'url': response.request.url if response.request is not None else kwargs['url'],
            'method': method,
            'headers': dict(response.request.headers) if response.request is not None else kwargs['headers'],
            'params': query_dict(kwargs['params']),
            'body': response_body_preview(kwargs),
        },
    }


def query_dict(pairs):
    """Keep duplicate query keys visible instead of silently collapsing them."""
    result = {}
    for key, value in pairs:
        if key in result:
            current = result[key]
            result[key] = current + [value] if isinstance(current, list) else [current, value]
        else:
            result[key] = value
    return result


def response_body_preview(kwargs):
    body = kwargs.get('json') if kwargs.get('json') is not None else kwargs.get('data')
    if isinstance(body, bytes):
        return f'[binary {len(body)} bytes]'
    return body
