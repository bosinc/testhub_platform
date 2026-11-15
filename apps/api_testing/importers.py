"""Convert collections into request DTOs with separate, non-executable preview metadata."""

from copy import deepcopy
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit


HTTP_METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
PATH_PARAMETER_RE = re.compile(r'(?<!\{)\{([^{}]+)\}(?!\})')
VARIABLE_RE = re.compile(r'\{\{([^{}]+)\}\}')
ENTRY_TYPES = {'string', 'text', 'integer', 'number', 'boolean', 'json', 'file'}
DEFAULT_SETTINGS = {'timeout': 30, 'connect_timeout': 5, 'verify_ssl': True, 'follow_redirects': True}


def _resolve(document, value, depth=0):
    if not isinstance(value, dict) or '$ref' not in value or depth > 20:
        return value
    reference = value['$ref']
    if not isinstance(reference, str) or not reference.startswith('#/'):
        return value
    target = document
    for part in reference[2:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        target = target.get(part, {}) if isinstance(target, dict) else {}
    if not isinstance(target, dict):
        return value
    target = _resolve(document, target, depth + 1)
    return {**target, **{k: v for k, v in value.items() if k != '$ref'}}


def _example(schema):
    if not isinstance(schema, dict):
        return None
    if 'example' in schema:
        return schema['example']
    if 'default' in schema:
        return schema['default']
    if schema.get('enum'):
        return schema['enum'][0]
    kind = schema.get('type')
    if kind == 'object' or 'properties' in schema:
        return {name: _example(prop) for name, prop in schema.get('properties', {}).items()}
    if kind == 'array':
        item = _example(schema.get('items', {}))
        return [item] if item is not None else []
    return {'string': '', 'integer': 0, 'number': 0, 'boolean': False}.get(kind)


def _description(value):
    if isinstance(value, dict):
        value = value.get('content', '')
    return value if isinstance(value, str) else ''


def _entry(key, value='', enabled=True, kind='string', description=''):
    if isinstance(value, (dict, list)) and kind != 'file':
        kind = 'json'
    return {'key': str(key), 'value': deepcopy(value), 'enabled': bool(enabled),
            'type': kind if kind in ENTRY_TYPES else 'string',
            'description': _description(description)}


def _path_entries(entries, warnings, blocking_errors):
    seen = set()
    for parameter in entries:
        key, value = parameter['key'], parameter['value']
        if not key or key in seen or isinstance(value, (dict, list)):
            blocking_errors.append(f'路径参数 {key} 为空、重复或不是标量，需要修正后再导入。')
        seen.add(key)
        if value is None or value == '':
            parameter['enabled'] = False
            warnings.append(f'路径参数 {key} 未提供值，导入为禁用条目；执行前需要填写并启用。')
    return entries


def _preflight(item):
    warnings, errors = item['warnings'], item['blocking_errors']
    path = re.sub(r'\{\{(?:env|vars|steps|loop)\.[A-Za-z0-9_.-]+\}\}', 'variable', item['url'])
    path = re.sub(r'(?<!\{)\{[A-Za-z_][\w.-]*\}(?!\})', 'parameter', path)
    if '{' in path or '}' in path:
        errors.append('路径包含未知变量或无效占位符，需要手工确认变量来源后再导入。')
    decoded = path
    while unquote(decoded) != decoded:
        decoded = unquote(decoded)
    if decoded.startswith('//') or '..' in decoded.split('/') or re.search(r'[\x00-\x20\x7f-\x9f\\]', decoded):
        errors.append('请求路径包含非法地址或上级路径段，需要修正后再导入。')
    for field in ('headers', 'params'):
        for index, parameter in enumerate(item[field]):
            if parameter['enabled'] and not parameter['key']:
                errors.append(f'{field}.{index} 的启用条目缺少 key，需要修正后再导入。')
    warnings.extend(error for error in errors if error not in warnings)
    return item


def _entries(source):
    if not isinstance(source, list):
        return []
    return [_entry(p['key'], p.get('value', ''), not p.get('disabled', False),
                   p.get('type', 'string'), p.get('description', ''))
            for p in source if isinstance(p, dict) and p.get('key') is not None]


def _path_only(url):
    """Strip the authority without removing any known request path prefix."""
    value = url.strip() if isinstance(url, str) else ''
    if not value:
        return '/'
    if '://' in value or value.startswith('//'):
        value = urlsplit(value).path or '/'
    else:
        value = re.sub(r'^\{\{[^{}]+\}\}', '', value)
        value = value.split('?', 1)[0].split('#', 1)[0]
    if not value.startswith('/'):
        value = f'/{value}'
    return re.sub(r'%7([bd])', lambda match: '{' if match[1].lower() == 'b' else '}',
                  value, flags=re.IGNORECASE) or '/'


def _path_parameter_names(path):
    names = []
    for match in PATH_PARAMETER_RE.finditer(path or ''):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def _query_entries(url):
    query = url.split('?', 1)[1].split('#', 1)[0] if '?' in url else ''
    return [_entry(key, value) for key, value in parse_qsl(query, keep_blank_values=True)]


def _suggestion(value, warnings, origin_only=False):
    """Only suggest concrete HTTP(S) environment URLs; never resolve unknown hosts."""
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('http', 'https') and parts.hostname
                 and not parts.username and not parts.password and not parts.query
                 and not parts.fragment and parts.port != 0
                 and not any(marker in value for marker in ('?', '#', '{', '}'))
                 and not re.search(r'[\s{}\\]', value)
                 and '..' not in unquote(parts.path).split('/')
                 and not parts.netloc.endswith(':'))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        warnings.append('环境建议不是具体、有效的 HTTP(S) 地址，需要手工设置环境前置 URL。')
        return None
    path = '' if origin_only else parts.path.rstrip('/')
    return {'base_url': urlunsplit((parts.scheme, parts.netloc, path, '', ''))}


def _openapi_environments(document, path_item, operation, warnings):
    suggestions = []
    if 'swagger' in document:
        schemes = operation.get('schemes', document.get('schemes', []))
        host = document.get('host', '')
        for scheme in schemes:
            suggestion = _suggestion(f'{scheme}://{host}{document.get("basePath", "")}', warnings)
            if suggestion and suggestion not in suggestions:
                suggestions.append(suggestion)
        warnings.append('Swagger basePath 属于环境前置 URL；请求仅保留 paths 声明的路径，不重复添加 basePath。')
    else:
        servers = operation.get('servers', path_item.get('servers', document.get('servers', [])))
        for server in servers:
            if not isinstance(server, dict):
                continue
            value = server.get('url', '')
            variables = server.get('variables', {})
            if isinstance(value, str):
                for name, variable in variables.items():
                    if isinstance(variable, dict) and 'default' in variable:
                        value = value.replace('{' + name + '}', str(variable['default']))
                suggestion = _suggestion(value, warnings)
                if suggestion and suggestion not in suggestions:
                    suggestions.append(suggestion)
        warnings.append('OpenAPI server 的基础路径保留在环境前置 URL；请求使用 paths 本身，不额外添加 basePath 前缀。')
    if not suggestions:
        warnings.append('没有具体的服务器 URL，需要手工设置环境前置 URL。')
    return suggestions


def _auth(raw, warnings, source_meta):
    if not isinstance(raw, dict) or not raw:
        return {'type': 'none'}
    source_meta['auth'] = deepcopy(raw)
    kind = raw.get('type', 'none')
    if kind in ('none', 'noauth'):
        return {'type': 'none'}
    config = raw.get(kind, {})
    if isinstance(config, list):
        config = {p['key']: p.get('value', '') for p in config
                  if isinstance(p, dict) and p.get('key')}
    if not isinstance(config, dict):
        config = {}
    if kind == 'basic':
        return {'type': 'basic', 'username': config.get('username', ''),
                'password': config.get('password', '')}
    if kind == 'bearer':
        return {'type': 'bearer', 'token': config.get('token', '')}
    if kind in ('apikey', 'api_key'):
        location = config.get('in', 'header')
        if location not in ('header', 'query'):
            warnings.append(f'不支持 API Key 位置 {location}；原认证配置保留在预览 source_meta.auth。')
            return {'type': 'none'}
        return {'type': 'api_key', 'key': config.get('key', ''),
                'value': config.get('value', ''), 'in': location}
    warnings.append(f'不支持认证类型 {kind}；原认证配置保留在预览 source_meta.auth，需要手工配置。')
    return {'type': 'none'}


def _openapi_auth(document, operation, warnings, source_meta):
    requirements = operation.get('security', document.get('security', []))
    if not requirements:
        return {'type': 'none'}
    source_meta['security'] = deepcopy(requirements)
    schemes = document.get('components', {}).get('securitySchemes', document.get('securityDefinitions', {}))
    if len(requirements) > 1:
        warnings.append('OpenAPI 包含多个可选认证方案，仅映射第一个；其余保留在 source_meta.security。')
    requirement = requirements[0]
    if not isinstance(requirement, dict) or not requirement:
        return {'type': 'none'}
    if len(requirement) > 1:
        warnings.append('OpenAPI 联合认证无法映射为单个 DTO；认证要求保留在 source_meta.security，需要手工配置。')
        source_meta['security_schemes'] = deepcopy(schemes)
        return {'type': 'none'}
    name = next(iter(requirement))
    scheme = _resolve(document, schemes.get(name, {}))
    source_meta['security_schemes'] = {name: deepcopy(scheme)}
    kind = scheme.get('type')
    if kind == 'basic' or (kind == 'http' and scheme.get('scheme', '').lower() == 'basic'):
        raw = {'type': 'basic', 'basic': {'username': '', 'password': ''}}
    elif kind == 'http' and scheme.get('scheme', '').lower() == 'bearer':
        raw = {'type': 'bearer', 'bearer': {'token': ''}}
    elif kind == 'apiKey':
        raw = {'type': 'apikey', 'apikey': {'key': scheme.get('name', ''), 'value': '',
                                           'in': scheme.get('in', 'header')}}
    else:
        warnings.append(f'不支持 OpenAPI 认证 {name} ({kind})；配置保留在 source_meta.security_schemes。')
        return {'type': 'none'}
    warnings.append(f'OpenAPI 认证 {name} 未提供凭证，需要手工填写或绑定变量。')
    return _auth(raw, warnings, source_meta)


def _file_preview(source_meta, blocking_errors, warnings, key, index, source_path):
    """A source path is metadata, never a file to open or a DTO value."""
    source_meta.setdefault('files', []).append(
        {'key': key, 'index': index, 'source_path': deepcopy(source_path)})
    message = f'文件字段 {key} 包含本地 source_path，不能导入执行；请先上传文件并替换为受控文件引用。'
    blocking_errors.append(message)
    warnings.append(message)


def _openapi_body(document, operation, parameters, headers, warnings):
    body = {'type': 'none'}
    form = []
    consumes = operation.get('consumes', document.get('consumes', ['application/json']))
    content_type = consumes[0] if consumes else 'application/json'
    for parameter in parameters:
        if parameter.get('in') == 'body':
            schema = _resolve(document, parameter.get('schema', {}))
            body = {'type': 'json', 'data': parameter.get('example', _example(schema))}
        elif parameter.get('in') == 'formData':
            kind = parameter.get('type', 'string')
            value = parameter.get('example', _example(parameter))
            form.append(_entry(parameter['name'], '' if value is None else value,
                               kind=kind, description=parameter.get('description', '')))
    if form:
        body = {'type': 'form-data' if content_type == 'multipart/form-data' else 'x-www-form-urlencoded',
                'data': form}
    request_body = _resolve(document, operation.get('requestBody', {}))
    content = request_body.get('content', {}) if isinstance(request_body, dict) else {}
    if len(content) > 1:
        warnings.append('请求体有多个媒体类型，仅导入第一个，请核对 Content-Type。')
    for content_type, media in content.items():
        if not isinstance(media, dict):
            continue
        schema = _resolve(document, media.get('schema', {}))
        data = media.get('example', _example(schema))
        if 'example' not in media and media.get('examples'):
            example = _resolve(document, next(iter(media['examples'].values())))
            data = example.get('value', data)
        if content_type in ('multipart/form-data', 'application/x-www-form-urlencoded'):
            entries = []
            for key, prop in schema.get('properties', {}).items():
                prop = _resolve(document, prop)
                kind = 'file' if prop.get('format') in ('binary', 'base64') else prop.get('type', 'string')
                value = data.get(key, '') if isinstance(data, dict) else _example(prop)
                entries.append(_entry(key, '' if kind == 'file' else value, kind=kind,
                                      description=prop.get('description', '')))
            body = {'type': 'form-data' if content_type == 'multipart/form-data' else 'x-www-form-urlencoded',
                    'data': entries}
            if media.get('encoding'):
                warnings.append('OpenAPI 表单 encoding 配置未映射，需要手工核对字段编码。')
        else:
            if 'json' not in content_type and not isinstance(data, str):
                data = json.dumps(data) if data is not None else ''
            body = {'type': 'json' if 'json' in content_type else 'raw',
                    'data': data if data is not None else ''}
        break
    if body['type'] != 'none' and not any(h['key'].lower() == 'content-type' for h in headers):
        headers.append(_entry('Content-Type', content_type))
    if form and any(p['type'] == 'file' for p in form):
        warnings.append('OpenAPI 文件字段未提供文件内容，需要上传文件并配置受控文件引用。')
    if body['type'] == 'form-data' and any(p['type'] == 'file' for p in body['data']):
        if not form:
            warnings.append('OpenAPI 文件字段未提供文件内容，需要上传文件并配置受控文件引用。')
    return body


def _openapi(document):
    if not isinstance(document.get('paths'), dict):
        raise ValueError('OpenAPI/Swagger 文档缺少 paths')
    requests = []
    info = document.get('info')
    document_name = str(info.get('title') or '').strip() if isinstance(info, dict) else ''
    for path, path_item in document['paths'].items():
        path_item = _resolve(document, path_item)
        if not isinstance(path_item, dict):
            continue
        inherited = [_resolve(document, p) for p in path_item.get('parameters', [])]
        for method, operation in path_item.items():
            method = method.upper()
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            warnings, blocking_errors, source_meta = [], [], {}
            declared = [_resolve(document, p) for p in operation.get('parameters', [])]
            overrides = {(p.get('in'), p.get('name')) for p in declared if isinstance(p, dict)}
            parameters = [p for p in inherited if isinstance(p, dict)
                          and (p.get('in'), p.get('name')) not in overrides] + declared
            parameters = [p for p in parameters if isinstance(p, dict) and p.get('name')]
            url = _path_only(path)
            params, headers, path_params = _query_entries(path), [], []
            for parameter in parameters:
                schema = _resolve(document, parameter.get('schema', parameter))
                entry = _entry(parameter['name'], parameter.get('example', _example(schema)),
                               kind=schema.get('type', 'string'), description=parameter.get('description', ''))
                if entry['value'] is None:
                    entry['value'] = ''
                location = parameter.get('in')
                if location in ('query', 'path', 'header'):
                    {'query': params, 'path': path_params, 'header': headers}[location].append(entry)
                    if parameter.get('style') or parameter.get('explode') is not None or parameter.get('collectionFormat'):
                        warnings.append(f'参数 {parameter["name"]} 的特殊序列化规则未映射，需要手工核对。')
                elif location not in ('body', 'formData'):
                    warnings.append(f'不支持参数位置 {location} ({parameter["name"]})，需要手工配置。')
            for name in _path_parameter_names(url):
                if not any(p['key'] == name for p in path_params):
                    path_params.append(_entry(name))
            path_params = _path_entries(path_params, warnings, blocking_errors)
            body = _openapi_body(document, operation, parameters, headers, warnings)
            if body['type'] in ('form-data', 'x-www-form-urlencoded') and any(p['type'] == 'file' for p in body['data']):
                blocking_errors.append('OpenAPI 文件字段缺少文件内容，需要上传文件后才能导入。')
            suggestions = _openapi_environments(document, path_item, operation, warnings)
            auth = _openapi_auth(document, operation, warnings, source_meta)
            tags = operation.get('tags') or ['Imported APIs']
            group_parts = ([document_name] if document_name else []) + [str(tags[0])]
            requests.append(_preflight({
                'name': operation.get('summary') or operation.get('operationId') or f'{method} {path}',
                'description': _description(operation.get('description', '')), 'method': method,
                'url': url, 'headers': headers, 'params': params, 'path_params': path_params, 'body': body,
                'request_type': 'HTTP', 'auth': auth, 'scripts': {'pre_request': '', 'post_request': ''},
                'assertions': [], 'extractors': [], 'settings': deepcopy(DEFAULT_SETTINGS),
                'group': ' / '.join(group_parts), 'group_path': group_parts, 'path': url,
                'environment_suggestions': suggestions, 'warnings': warnings, 'blocking_errors': blocking_errors,
                'source_meta': source_meta,
            }))
    if not requests:
        raise ValueError('文档中没有可导入的 HTTP 接口')
    return requests


def _postman_url(url):
    if isinstance(url, str):
        return url
    if not isinstance(url, dict):
        return ''
    if url.get('raw'):
        return url['raw']
    host, path = url.get('host', []), url.get('path', [])
    host = '.'.join(host) if isinstance(host, list) else host
    path = '/'.join(path) if isinstance(path, list) else path
    if not host:
        return '/' + path.lstrip('/')
    if host.startswith('{{'):
        return host + '/' + path.lstrip('/')
    protocol = url.get('protocol')
    return f'{protocol + ":" if protocol else ""}//{host}/{path.lstrip("/")}'


def _postman_location(url_data, raw, variables, warnings):
    resolved = raw
    unresolved = []
    def replace(match):
        name = match[1]
        value = variables.get(name)
        if isinstance(value, str) and value and not VARIABLE_RE.search(value):
            return value
        unresolved.append(name)
        return match[0]
    # Resolve only the authority token, not normal runtime path/query variables.
    authority = re.match(r'^(?:https?://)?\{\{[^{}]+\}\}', raw)
    if authority:
        resolved = VARIABLE_RE.sub(replace, raw[:authority.end()]) + raw[authority.end():]
    path_source = resolved if resolved.startswith(('http://', 'https://', '//')) else raw
    request_path = _path_only(path_source)
    # Postman colon placeholders also occur in raw string URLs.
    request_path = re.sub(r'(?<=/):([A-Za-z_][\w.-]*)(?=/|$)', r'{\1}', request_path)
    suggestions = []
    if unresolved:
        warnings.append(f'未知主机变量 {", ".join(unresolved)}，不能猜测 URL，需要手工设置环境前置 URL。')
    elif resolved.startswith(('http://', 'https://')):
        parts = urlsplit(resolved)
        # Query belongs to the DTO, so it must not invalidate an otherwise valid origin.
        origin = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
        suggestion = _suggestion(origin, warnings, origin_only=True)
        if suggestion:
            suggestions.append(suggestion)
    else:
        warnings.append('没有具体的 HTTP(S) origin，需要手工设置环境前置 URL。')
    warnings.append('Postman 仅建议 origin，全部 path（包括 /v1）保留在请求；如移入环境基础路径，请手工同步调整，避免重复前缀。')
    return request_path, suggestions


def _postman_body(raw_body, warnings, blocking_errors, source_meta):
    if not isinstance(raw_body, dict) or not raw_body or raw_body.get('disabled'):
        return {'type': 'none'}
    mode = raw_body.get('mode')
    if mode == 'raw':
        raw = raw_body.get('raw', '')
        language = raw_body.get('options', {}).get('raw', {}).get('language')
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            if language == 'json':
                warnings.append('JSON 请求体包含模板或非法 JSON，保留为 raw，请核对后再执行。')
            return {'type': 'raw', 'data': raw}
        return {'type': 'json', 'data': data} if language in (None, 'json') else {'type': 'raw', 'data': raw}
    if mode in ('urlencoded', 'formdata'):
        source = raw_body.get(mode, [])
        entries = _entries(source)
        entry_index = 0
        for index, parameter in enumerate(source):
            if not isinstance(parameter, dict):
                continue
            if parameter.get('type') == 'file' or parameter.get('src'):
                if parameter.get('src'):
                    _file_preview(source_meta, blocking_errors, warnings, parameter.get('key', ''),
                                  index, parameter['src'])
                else:
                    message = f'文件字段 {parameter.get("key", "")} 缺少文件内容，需要上传文件后才能导入。'
                    warnings.append(message)
                    blocking_errors.append(message)
                if parameter.get('key') is not None:
                    entries[entry_index]['type'] = 'file'
            if parameter.get('key') is not None:
                entry_index += 1
        for entry in entries:
            if entry['type'] == 'file':
                entry['value'] = ''
        return {'type': 'x-www-form-urlencoded' if mode == 'urlencoded' else 'form-data', 'data': entries}
    if mode == 'file':
        _file_preview(source_meta, blocking_errors, warnings, 'body', 0, raw_body.get('file', {}).get('src'))
    else:
        warnings.append(f'不支持请求体模式 {mode}；原配置保留在预览 source_meta.body。')
    source_meta['body'] = deepcopy(raw_body)
    return {'type': 'none'}


def _postman_scripts(events, warnings, source_meta):
    scripts = {'prerequest': [], 'test': []}
    if events:
        source_meta['events'] = deepcopy(events)
    for event in events:
        if not isinstance(event, dict) or event.get('disabled'):
            continue
        kind = event.get('listen')
        script = event.get('script', {})
        if kind not in scripts or script.get('type', 'text/javascript') != 'text/javascript':
            warnings.append(f'不支持 Postman 事件 {kind} 或脚本语言，原配置保留在 source_meta.events。')
            continue
        code = script.get('exec', [])
        code = '\n'.join(code) if isinstance(code, list) else code
        if script.get('src'):
            warnings.append('Postman 外部脚本不会读取或下载，需要手工配置；来源保留在 source_meta.events。')
        if code:
            scripts[kind].append(code)
    if any(scripts.values()):
        warnings.append('Postman 脚本按集合、文件夹、请求顺序保留；pm/postman API、断言和提取语义需要核对运行时兼容性。')
    return '\n'.join(scripts['prerequest']), '\n'.join(scripts['test'])


def _postman_settings(profile, warnings, source_meta):
    if profile:
        source_meta['protocol_profile_behavior'] = deepcopy(profile)
    settings = deepcopy(DEFAULT_SETTINGS)
    mapping = {'followRedirects': ('follow_redirects', False),
               'disableFollowRedirects': ('follow_redirects', True),
               'strictSSL': ('verify_ssl', False)}
    for key, value in profile.items():
        if key in mapping and isinstance(value, bool):
            target, invert = mapping[key]
            settings[target] = not value if invert else value
        elif key == 'requestTimeout' and isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 300000:
            settings['timeout'] = value / 1000
        else:
            warnings.append(f'不支持 Postman 设置 {key}，保留在 source_meta.protocol_profile_behavior，需要手工核对。')
    return settings


def _postman(document):
    items = document.get('item')
    if not isinstance(items, list):
        raise ValueError('Postman Collection 文档缺少 item')
    requests = []
    info = document.get('info')
    collection_name = str(info.get('name') or '').strip() if isinstance(info, dict) else ''

    def visit(nodes, folders, inherited_auth, events, profile, variables):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            auth = node.get('auth') if node.get('auth') is not None else inherited_auth
            node_events = events + node.get('event', [])
            node_profile = {**profile, **node.get('protocolProfileBehavior', {})}
            node_variables = {**variables, **{p['key']: p.get('value', '') for p in node.get('variable', [])
                                             if isinstance(p, dict) and p.get('key') and not p.get('disabled')}}
            if isinstance(node.get('item'), list):
                visit(node['item'], folders + [node.get('name') or 'Folder'], auth,
                      node_events, node_profile, node_variables)
                continue
            request = node.get('request', {})
            if not isinstance(request, dict):
                continue
            method = str(request.get('method', 'GET')).upper()
            if method not in HTTP_METHODS:
                continue
            warnings, blocking_errors, source_meta = [], [], {}
            headers = _entries(request.get('header', []))
            url_data = request.get('url', '')
            raw_url = _postman_url(url_data)
            request_path, suggestions = _postman_location(url_data, raw_url, node_variables, warnings)
            if isinstance(url_data, dict) and 'query' in url_data:
                params = _entries(url_data['query'])
            else:
                params = _query_entries(raw_url)
            path_params = _entries(url_data.get('variable', [])) if isinstance(url_data, dict) else []
            for name in _path_parameter_names(request_path):
                if not any(p['key'] == name for p in path_params):
                    path_params.append(_entry(name))
            path_params = _path_entries(path_params, warnings, blocking_errors)
            body = _postman_body(request.get('body', {}), warnings, blocking_errors, source_meta)
            request_auth = request.get('auth') if request.get('auth') is not None else auth
            dto_auth = _auth(request_auth, warnings, source_meta)
            pre_script, post_script = _postman_scripts(node_events + request.get('event', []), warnings, source_meta)
            settings = _postman_settings({**node_profile, **request.get('protocolProfileBehavior', {})}, warnings, source_meta)
            group_path = ([collection_name] if collection_name else []) + folders
            requests.append(_preflight({
                'name': node.get('name') or f'{method} {request_path}',
                'description': _description(request.get('description', '')), 'method': method,
                'url': request_path, 'headers': headers, 'params': params, 'path_params': path_params,
                'body': body, 'request_type': 'HTTP', 'auth': dto_auth,
                'scripts': {'pre_request': pre_script, 'post_request': post_script},
                'assertions': [], 'extractors': [], 'settings': settings,
                'group': ' / '.join(group_path) if group_path else 'Imported APIs',
                'group_path': group_path or ['Imported APIs'], 'path': request_path,
                'environment_suggestions': suggestions, 'warnings': warnings,
                'blocking_errors': blocking_errors, 'source_meta': source_meta,
            }))

    variables = {p['key']: p.get('value', '') for p in document.get('variable', [])
                 if isinstance(p, dict) and p.get('key') and not p.get('disabled')}
    visit(items, [], document.get('auth'), document.get('event', []),
          document.get('protocolProfileBehavior', {}), variables)
    if not requests:
        raise ValueError('Postman Collection 中没有可导入的 HTTP 接口')
    return requests


def parse_collection(raw, file_name):
    """Return a list of request DTOs plus warnings, suggestions and blocking_errors.

    Preview metadata (source_meta, environment_suggestions, warnings, blocking_errors)
    is not part of the request DTO. Import views must reject selected blocked items
    before persistence, and must never pass local source paths to the executor.
    """
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('文件必须使用 UTF-8 编码') from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
            document = yaml.safe_load(text)
        except ImportError as exc:
            raise ValueError('无法解析 YAML，请使用 JSON 格式') from exc
        except Exception as exc:
            raise ValueError('文件不是有效的 JSON 或 YAML') from exc
    if not isinstance(document, dict):
        raise ValueError('文件内容必须是对象')
    if 'openapi' in document or 'swagger' in document:
        return _openapi(document)
    if 'info' in document and 'item' in document:
        return _postman(document)
    raise ValueError(f'不支持的接口定义格式：{file_name}')
