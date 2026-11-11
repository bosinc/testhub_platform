"""Helpers for converting OpenAPI and Postman collections into API requests."""

import json
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit


HTTP_METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
PATH_PARAMETER_RE = re.compile(r'(?<!\{)\{([^{}]+)\}(?!\})')
BASE_URL_VARIABLE = '{{baseUrl}}'
SERVER_VARIABLE_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')


def _server_base_variable(server):
    """Pick the TestHub base-URL placeholder for an OpenAPI server.

    OpenAPI lets a server embed a variable, e.g. ``https://{adminurl}.example.com``.
    We surface that variable as ``{{adminurl}}`` so TestHub resolves the real host
    from the active environment (matching multi-gateway specs such as a public API
    plus an admin gateway). Servers with a concrete URL (the common case) fall back
    to ``{{baseUrl}}`` so existing imports stay unchanged.
    """
    url = server.get('url', '') if isinstance(server, dict) else ''
    match = SERVER_VARIABLE_RE.search(url)
    if match:
        return '{{' + match.group(1) + '}}'
    return BASE_URL_VARIABLE


def _resolve(document, value, depth=0):
    if not isinstance(value, dict) or '$ref' not in value or depth > 20:
        return value
    reference = value['$ref']
    if not reference.startswith('#/'):
        return value
    target = document
    for part in reference[2:].split('/'):
        target = target.get(part, {}) if isinstance(target, dict) else {}
    if not isinstance(target, dict):
        return value
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


def _path_only(url):
    """Keep only the request path; the selected TestHub environment supplies the host."""
    if not isinstance(url, str):
        return ''
    value = url.strip()
    if not value:
        return ''
    if value.startswith(('http://', 'https://')):
        path = urlsplit(value).path or '/'
        return path.replace('%7B', '{').replace('%7D', '}').replace('%7b', '{').replace('%7d', '}')
    # Postman commonly stores an environment token before the path.
    value = re.sub(r'^\{\{[^{}]+\}\}', '', value)
    if not value.startswith('/'):
        value = f'/{value}'
    return value.split('?', 1)[0].split('#', 1)[0].replace('%7B', '{').replace('%7D', '}').replace('%7b', '{').replace('%7d', '}') or '/'


def _path_parameter_names(path):
    """Return single-brace OpenAPI path variables in declaration order."""
    names = []
    for match in PATH_PARAMETER_RE.finditer(path or ''):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def _import_url(path, base_variable=BASE_URL_VARIABLE):
    """Prefix imported paths with the environment base URL variable.

    ``base_variable`` is normally ``{{baseUrl}}`` but may be any server variable
    declared by the spec (e.g. ``{{adminurl}}``) so multi-gateway APIs import with
    the correct host placeholder.
    """
    normalized_path = _path_only(path)
    return f'{base_variable}{normalized_path}'


def _openapi(document):
    if not isinstance(document.get('paths'), dict):
        raise ValueError('OpenAPI/Swagger 文档缺少 paths')
    requests = []
    info = document.get('info')
    document_name = str(info.get('title') or '').strip() if isinstance(info, dict) else ''
    servers = document.get('servers') or []
    default_base = _server_base_variable(servers[0]) if servers else BASE_URL_VARIABLE
    for path, path_item in document['paths'].items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = [_resolve(document, p) for p in path_item.get('parameters', [])]
        for method, operation in path_item.items():
            method = method.upper()
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            parameters = path_parameters + [_resolve(document, p) for p in operation.get('parameters', [])]
            op_servers = operation.get('servers') or []
            base_variable = _server_base_variable(op_servers[0]) if op_servers else default_base
            params, headers, body = {}, {}, {}
            for parameter in parameters:
                if not isinstance(parameter, dict) or not parameter.get('name'):
                    continue
                value = parameter.get('example', _example(parameter.get('schema', {})))
                if parameter.get('in') == 'query':
                    params[parameter['name']] = value if value is not None else ''
                elif parameter.get('in') == 'path':
                    params[parameter['name']] = value if value is not None else ''
                elif parameter.get('in') == 'header':
                    headers[parameter['name']] = value if value is not None else ''
                elif parameter.get('in') == 'body':
                    body = {'type': 'json', 'data': value if value is not None else {}}
            # Some exported Swagger files contain `{name}` in the path but omit
            # the corresponding parameter declaration. Keep the URL usable by
            # adding those variables with an empty value as a fallback.
            for name in _path_parameter_names(path):
                params.setdefault(name, '')
            # OpenAPI 路径参数使用单花括号；保留原格式，避免被误当成环境变量。
            url = _import_url(path, base_variable)
            request_body = _resolve(document, operation.get('requestBody', {}))
            content = request_body.get('content', {}) if isinstance(request_body, dict) else {}
            for content_type, media in content.items():
                if not isinstance(media, dict):
                    continue
                body = {'type': 'json' if 'json' in content_type else 'raw',
                        'data': media.get('example', _example(_resolve(document, media.get('schema', {}))))}
                if body['data'] is None:
                    body['data'] = ''
                headers.setdefault('Content-Type', content_type)
                break
            if not content and body:
                consumes = document.get('consumes') or ['application/json']
                headers.setdefault('Content-Type', consumes[0])
            tags = operation.get('tags') or ['Imported APIs']
            group_parts = [document_name] if document_name else []
            group_parts.append(str(tags[0]))
            requests.append({
                'name': operation.get('summary') or operation.get('operationId') or f'{method} {path}',
                'description': operation.get('description', ''), 'method': method, 'url': url,
                'headers': headers, 'params': params, 'body': body,
                'group': ' / '.join(group_parts), 'group_path': group_parts, 'path': path,
            })
    if not requests:
        raise ValueError('文档中没有可导入的 HTTP 接口')
    return requests


def _postman_url(url):
    if isinstance(url, str):
        return url
    if not isinstance(url, dict):
        return ''
    raw = url.get('raw')
    if raw:
        return raw
    host = url.get('host', [])
    path = url.get('path', [])
    return '/'.join([*(host if isinstance(host, list) else [host]),
                     *(path if isinstance(path, list) else [path])])


def _postman_query(url_data, url):
    params = {}
    if isinstance(url_data, dict) and url_data.get('query'):
        params.update({p.get('key'): p.get('value', '') for p in url_data['query']
                       if isinstance(p, dict) and p.get('key') and not p.get('disabled')})
    else:
        params.update(dict(parse_qsl(urlsplit(url).query, keep_blank_values=True)))
    if isinstance(url_data, dict) and isinstance(url_data.get('variable'), list):
        params.update({p.get('key'): p.get('value', '') for p in url_data['variable']
                       if isinstance(p, dict) and p.get('key') and not p.get('disabled')})
    return params


def _postman(document):
    items = document.get('item')
    if not isinstance(items, list):
        raise ValueError('Postman Collection 文档缺少 item')
    requests = []
    collection_name = ''
    info = document.get('info')
    if isinstance(info, dict):
        collection_name = str(info.get('name') or '').strip()

    def visit(nodes, folders):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if isinstance(node.get('item'), list):
                visit(node['item'], folders + [node.get('name') or 'Folder'])
                continue
            request = node.get('request', {})
            if not isinstance(request, dict):
                continue
            method = str(request.get('method', 'GET')).upper()
            if method not in HTTP_METHODS:
                continue
            headers = {h.get('key'): h.get('value', '') for h in request.get('header', [])
                       if isinstance(h, dict) and h.get('key') and not h.get('disabled')}
            url_data = request.get('url', '')
            request_url = _postman_url(url_data)
            params = _postman_query(url_data, request_url)
            if params:
                parts = urlsplit(request_url)
                request_url = urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
            request_path = _path_only(request_url)
            request_url = f'{BASE_URL_VARIABLE}{request_path}'
            body = {}
            raw_body = request.get('body') or {}
            mode = raw_body.get('mode')
            if mode == 'raw':
                raw = raw_body.get('raw', '')
                try:
                    body = {'type': 'json', 'data': json.loads(raw)}
                except (TypeError, ValueError):
                    body = {'type': 'raw', 'data': raw}
            elif mode in ('urlencoded', 'formdata'):
                source = raw_body.get(mode, [])
                body = {'type': 'x-www-form-urlencoded' if mode == 'urlencoded' else 'form-data',
                        'data': {p.get('key'): p.get('value', '') for p in source
                                 if isinstance(p, dict) and p.get('key') and not p.get('disabled')}}
            group_path = ([collection_name] if collection_name else []) + folders
            requests.append({
                'name': node.get('name') or f'{method} {_postman_url(url_data)}',
                'description': request.get('description', ''), 'method': method,
                'url': request_url, 'headers': headers, 'params': params,
                'body': body,
                'group': ' / '.join(group_path) if group_path else 'Imported APIs',
                'group_path': group_path or ['Imported APIs'],
                'path': request_path,
            })

    # 优先保留 Postman JSON 中的集合名称，避免导入后根级接口落到默认分组。
    visit(items, [])
    if not requests:
        raise ValueError('Postman Collection 中没有可导入的 HTTP 接口')
    return requests


def parse_collection(raw, file_name):
    """Parse supported API definition files without making network requests."""
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
