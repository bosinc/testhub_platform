"""Environment access checks and environment-relative URL construction."""

from copy import deepcopy
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import ApiProject, Environment


PATH_PARAMETER_RE = re.compile(r'(?<!\{)\{([A-Za-z_][\w.-]*)\}(?!\})')
VARIABLE_RE = re.compile(r'\{\{\s*(?:env|vars|steps|loop)\.[A-Za-z0-9_.\-\[\]]+\s*\}\}')
SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
CONTROL_RE = re.compile(r'[\x00-\x20\x7f-\x9f]')


def _error(field, message, exception=ValidationError):
    raise exception({'errors': {field: [message]}})


def _decoded_path(value, field):
    # Repeated decoding prevents nested percent-encoding from hiding traversal.
    if CONTROL_RE.search(value):
        _error(field, _('地址不能包含空白或控制字符'))
    while True:
        if '\\' in value or re.search(r'[\x00-\x1f\x7f-\x9f]', value):
            _error(field, _('地址不能包含反斜杠、空白或控制字符'))
        if '..' in value.split('/'):
            _error(field, _('地址不能包含上级路径段'))
        decoded = unquote(value)
        if decoded == value:
            return value
        value = decoded


def validate_base_url(base_url, *, require_base_url=True):
    """Validate without resolving variables, trimming input or performing I/O."""
    if not isinstance(base_url, str):
        _error('base_url', _('前置URL必须是字符串'))
    if base_url == '' and not require_base_url:
        return base_url
    if not base_url:
        _error('base_url', _('执行前必须配置环境前置URL'))
    if any(marker in base_url for marker in ('?', '#', '{', '}')):
        _error('base_url', _('前置URL不能包含查询参数、片段或变量'))
    decoded = _decoded_path(base_url, 'base_url')
    try:
        parts = urlsplit(base_url)
        port = parts.port
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.netloc.endswith(':') or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        URLValidator(schemes=['http', 'https'])(base_url)
    except (ValueError, DjangoValidationError):
        _error('base_url', _('前置URL必须是有效的HTTP/HTTPS地址且不能包含用户认证信息'))
    if '{' in decoded or '}' in decoded:
        _error('base_url', _('前置URL不能包含变量'))
    return base_url


def validate_request_path(path, *, allow_variables=False):
    """Accept relative draft paths; execution rejects unresolved variables."""
    if not isinstance(path, str):
        _error('url', _('请求路径必须是字符串'))
    if '?' in path or '#' in path:
        _error('url', _('请求路径不能包含查询参数或片段，请使用params'))
    checked = VARIABLE_RE.sub('variable', path) if allow_variables else path
    if '{{' in checked or '}}' in checked:
        _error('url', _('请求路径包含未解析或无效的变量'))
    decoded = _decoded_path(checked, 'url')
    if decoded != checked and ('{' in decoded or '}' in decoded):
        _error('url', _('请求路径不能包含编码后的占位符'))
    if decoded.startswith('//') or SCHEME_RE.match(decoded):
        _error('url', _('请求路径必须是相对路径'))
    without_params = PATH_PARAMETER_RE.sub('parameter', checked)
    if '{' in without_params or '}' in without_params:
        _error('url', _('请求路径包含无效的占位符'))
    return path


def build_request_url(base_url, path, path_params=None, *, request_type='HTTP'):
    """Build a URL while retaining the environment's base path."""
    from .request_dto import normalize_entries

    validate_base_url(base_url)
    validate_request_path(path)
    if request_type not in ('HTTP', 'WEBSOCKET'):
        _error('request_type', _('不支持的请求类型'))
    entries = normalize_entries([] if path_params is None else path_params, 'path_params')
    values = {entry['key']: entry['value'] for entry in entries if entry['enabled']}
    names = set(PATH_PARAMETER_RE.findall(path))
    missing = names - values.keys()
    if missing:
        _error('path_params', _('缺少启用的路径参数: %(names)s') % {'names': ', '.join(sorted(missing))})
    unused = values.keys() - names
    if unused:
        _error('path_params', _('存在未使用的启用路径参数: %(names)s') % {'names': ', '.join(sorted(unused))})

    def substitute(match):
        value = str(values[match.group(1)])
        if '{{' in value or '}}' in value:
            _error('path_params', _('路径参数包含未解析的变量'))
        return quote(value, safe='')

    rendered = PATH_PARAMETER_RE.sub(substitute, path)
    # Parameter values such as '..' must not become navigation after encoding.
    decoded = _decoded_path(rendered, 'url')
    if '{{' in decoded or '}}' in decoded:
        _error('url', _('请求路径包含未解析的变量'))
    base = urlsplit(base_url)
    scheme = {'http': 'ws', 'https': 'wss'}[base.scheme] if request_type == 'WEBSOCKET' else base.scheme
    joined = base.path.rstrip('/') + '/' + rendered.lstrip('/')
    return urlunsplit((scheme, base.netloc, joined, '', ''))


class EnvironmentResolver:
    """No implicit active-environment lookup and no privileged-user bypass."""

    @staticmethod
    def _check_access(environment, *, user, project_id=None):
        if user is None or not user.is_authenticated:
            _error('environment_id', _('必须登录后才能访问环境'), PermissionDenied)
        if project_id is not None:
            project_id = EnvironmentResolver._identifier(project_id, 'project_id')
        if environment.scope == 'LOCAL':
            if project_id is None:
                _error('project_id', _('局部环境必须指定当前项目'))
            if environment.project_id != project_id:
                _error('environment_id', _('环境不属于当前项目'), PermissionDenied)
        elif environment.scope != 'GLOBAL':
            _error('environment_id', _('环境作用域无效'))

        # Query projects again so cached foreign keys cannot bypass soft deletion
        # or membership changes between resolution and snapshot creation.
        project_ids = {pk for pk in (project_id, environment.project_id) if pk is not None}
        for pk in project_ids:
            try:
                project = ApiProject.all_objects.select_related('workspace_project').get(pk=pk)
            except ApiProject.DoesNotExist:
                _error('project_id', _('关联项目不存在'), NotFound)
            if project.is_deleted or (project.workspace_project_id and project.workspace_project.is_deleted):
                _error('project_id', _('不能访问已删除项目的环境'), PermissionDenied)
            if (environment.scope == 'LOCAL' or pk == project_id) and (
                    project.owner_id != user.pk and not project.members.filter(pk=user.pk).exists()):
                _error('environment_id', _('没有项目环境访问权限'), PermissionDenied)

    @staticmethod
    def _identifier(value, field):
        if isinstance(value, bool) or not (
                isinstance(value, int) or isinstance(value, str) and re.fullmatch(r'[0-9]+', value)):
            _error(field, _('必须提供有效的正整数ID'))
        try:
            identifier = int(value)
        except (ValueError, OverflowError):
            _error(field, _('必须提供有效的正整数ID'))
        if not 1 <= identifier <= 9223372036854775807:
            _error(field, _('必须提供有效的正整数ID'))
        return identifier

    @staticmethod
    def resolve(environment_id, *, user, project_id=None, require_base_url=True):
        if environment_id is None or environment_id == '':
            _error('environment_id', _('必须明确选择环境'))
        identifier = EnvironmentResolver._identifier(environment_id, 'environment_id')
        try:
            environment = Environment.objects.get(pk=identifier)
        except Environment.DoesNotExist:
            _error('environment_id', _('环境不存在'), NotFound)
        EnvironmentResolver._check_access(environment, user=user, project_id=project_id)
        validate_base_url(environment.base_url, require_base_url=require_base_url)
        return environment

    @staticmethod
    def snapshot(environment, *, user, project_id=None):
        if not isinstance(environment, Environment) or environment.pk is None:
            _error('environment_id', _('必须提供已保存的环境'))
        EnvironmentResolver._check_access(environment, user=user, project_id=project_id)
        validate_base_url(environment.base_url)
        return deepcopy({'id': environment.pk, 'base_url': environment.base_url,
                         'variables': environment.variables})
