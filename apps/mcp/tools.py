"""MCP 工具实现（读工具即时返回，危险工具拆为 preview/confirm 两段）。

性能约定（与 BrickCore 落地设计对齐）：
- list_* 只查轻量字段（only/values）+ limit/offset 分页 + 60s 缓存（命中 < 20ms）
- 读工具同步即时返回；写工具 preview 同步、confirm 后异步执行返回 ID
- 每次调用写 McpCallLog（写失败不阻断响应），7 天日志节流清理

覆盖范围：项目 / 功能用例 / 接口测试（请求·场景用例·套件）/ UI 自动化
（用例·套件·元素库）/ 压测，写操作统一 preview → confirm 两段式。
"""
import json
import logging
import time

from django.core.cache import cache
from django.db import close_old_connections, connection

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from . import actions as act
from .actions import args_digest
from .auth import authenticate_headers, current_user_id
from .confirm import (ConfirmError, consume_pending, create_preview,
                      query_approval_status, verify_pending)
from .models import McpCallLog

logger = logging.getLogger(__name__)

#: list 类工具缓存 TTL（秒）：短 TTL 兼顾性能与数据新鲜度
LIST_CACHE_TTL = 60
#: 分页上限，防止 Agent 拉全表
MAX_LIMIT = 100


# --------------------------------------------------------------------- #
# 公共辅助
# --------------------------------------------------------------------- #

def _resolve_user(ctx: Context):
    """从 MCP 请求头解析当前用户（中间件已强制鉴权，此处落到具体用户）。"""
    try:
        request = ctx.request_context.request
        if request is not None:
            headers = {k.lower(): v for k, v in request.headers.items()}
            user = authenticate_headers(headers)
            if user is not None:
                return user
    except Exception:  # noqa: BLE001 - 兜底走 contextvar
        pass
    user_id = current_user_id.get()
    if user_id:
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.filter(id=user_id, is_active=True).first()
        if user:
            return user
    raise ToolError('未认证：请携带 Authorization: Bearer <JWT> 或 x-mcp-api-key 头')


def _resolve_client_name(ctx: Context) -> str:
    """从请求头提取客户端标识（User-Agent），失败返回空串。"""
    try:
        request = ctx.request_context.request
        if request is not None:
            headers = {k.lower(): v for k, v in request.headers.items()}
            return (headers.get('user-agent') or '')[:100]
    except Exception:  # noqa: BLE001
        pass
    return ''


def _clamp_page(limit: int, offset: int):
    limit = max(1, min(int(limit or 20), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    return limit, offset


def _folder_subtree_ids(folder_model, project_id, folder_id):
    """收集目录及其全部子目录 ID（广度优先，带环保护）。"""
    ids, frontier, guard = [folder_id], [folder_id], {folder_id}
    while frontier:
        frontier = [row for row in folder_model.objects.filter(
            project_id=project_id, parent_id__in=frontier
        ).values_list('id', flat=True) if row not in guard]
        guard.update(frontier)
        ids.extend(frontier)
    return ids


def _folder_index(folder_model, project_id):
    """构建 {目录 ID: {name, parent_id, path, level}} 索引，用于回填目录名与路径。"""
    rows = list(folder_model.objects.filter(project_id=project_id).values(
        'id', 'name', 'parent_id'))
    by_id = {row['id']: row for row in rows}
    index = {}
    for row in rows:
        names, cursor, guard = [row['name']], row['parent_id'], {row['id']}
        while cursor and cursor in by_id and cursor not in guard:
            guard.add(cursor)
            names.append(by_id[cursor]['name'])
            cursor = by_id[cursor]['parent_id']
        index[row['id']] = {
            'name': row['name'],
            'parent_id': row['parent_id'],
            'path': '/'.join(reversed(names)),
            'level': len(names) - 1,
        }
    return index


def _cache_get_or_set(key: str, builder):
    """60s 缓存包装；缓存不可用时直接回源，不影响正确性。"""
    try:
        hit = cache.get(key)
        if hit is not None:
            return json.loads(hit)
    except Exception:  # noqa: BLE001
        pass
    data = builder()
    try:
        cache.set(key, json.dumps(data, ensure_ascii=False, default=str), LIST_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return data


def _list_epoch(user_id) -> int:
    """当前用户的列表缓存版本号。

    写操作（confirm_*）成功后自增版本号，使该用户此前缓存的 list_* 结果整体失效。
    LocMem 后端不支持按前缀删除，用版本号前缀实现等价效果：
    「列表 → 发现缺失 → 创建 → 再列表」不会读到写前的旧数据。
    """
    try:
        return int(cache.get(f'mcp:list:epoch:{user_id}') or 0)
    except Exception:  # noqa: BLE001
        return 0


def _bump_list_epoch(user_id):
    """写操作后让该用户的 list_* 缓存失效（失败不影响主流程）。"""
    key = f'mcp:list:epoch:{user_id}'
    try:
        cache.set(key, _list_epoch(user_id) + 1, None)
    except Exception:  # noqa: BLE001
        pass


def _link_case_state(case_type: str, project_id: int):
    """返回 (有效关联的功能用例 ID 集合, 悬空关联的功能用例 ID 集合)。

    悬空 = 有 TestCaseAutomationLink 记录，但目标已删除、被移到其他项目或
    所属自动化项目已解绑统一项目。补齐流程必须把这类用例算作「未关联」。
    """
    from apps.testcases.models import TestCaseAutomationLink

    links = list(TestCaseAutomationLink.objects.filter(
        case_type=case_type, test_case__project_id=project_id
    ).values('test_case_id', 'object_id'))
    if not links:
        return set(), set()
    names = act.structural_target_names(
        case_type, project_id, {link['object_id'] for link in links})
    valid = {link['test_case_id'] for link in links if link['object_id'] in names}
    stale = {link['test_case_id'] for link in links} - valid
    return valid, stale


def _enrich_automation_links(links_by_case: dict, project_id: int):
    """把 target_exists 改成结构有效性，并补充 accessible（权限可见性）。"""
    for case_type in ('ui_test_case', 'api_scenario_case'):
        object_ids = {link['object_id']
                      for links in links_by_case.values() for link in links
                      if link.get('type') == case_type}
        names = act.structural_target_names(case_type, project_id, object_ids)
        for links in links_by_case.values():
            for link in links:
                if link.get('type') != case_type:
                    continue
                link['accessible'] = bool(link.get('target_exists'))
                link['target_exists'] = link['object_id'] in names
                if not link.get('target_name'):
                    link['target_name'] = names.get(link['object_id'], '')
    return links_by_case


def _log_call(user, tool_name: str, arguments: dict, started: float,
              error: str = '', status: str = 'success', client_name: str = ''):
    """写调用日志（含耗时/脱敏参数/客户端）；失败不阻断响应，并节流清理 7 天前日志。"""
    try:
        McpCallLog.objects.create(
            user=user if user and getattr(user, 'pk', None) else None,
            tool_name=tool_name[:100],
            args_digest=args_digest(arguments or {}),
            args_brief=act.mask_sensitive_args(_audit_arguments(tool_name, arguments or {})),
            client_name=client_name[:100],
            status=status,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=(error or '')[:2000],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug('MCP 调用日志写入失败: %s', exc)
    try:
        if cache.get('mcp:log_cleanup_at') is None:
            from datetime import timedelta
            from django.utils import timezone
            McpCallLog.objects.filter(
                created_at__lt=timezone.now() - timedelta(days=7)).delete()
            cache.set('mcp:log_cleanup_at', '1', 3600)
    except Exception:  # noqa: BLE001
        pass


def _audit_arguments(tool_name: str, arguments: dict) -> dict:
    """Keep imported source out of MCP call-log details while retaining its digest."""
    if tool_name not in {'preview_import_recorded_script', 'confirm_import_recorded_script'}:
        return arguments
    safe = dict(arguments)
    content = safe.pop('content', None)
    if content is not None:
        safe['content_bytes'] = len(content.encode('utf-8')) if isinstance(content, str) else 0
    if 'start_url' in safe:
        safe.pop('start_url')
        safe['has_start_url'] = True
    return safe


def _run_tool(ctx: Context, tool_name: str, arguments: dict, fn):
    """统一包装：鉴权 → 计时 → 执行 → 日志；错误转 ToolError。

    仅在非事务上下文中关闭旧连接：Django TestCase 的 atomic 块内关闭连接会
    破坏测试事务，使后续 ORM 查询失败被 _verify_jwt 吞掉而误判为「未认证」。
    """
    started = time.perf_counter()
    if not connection.in_atomic_block:
        close_old_connections()
    user = _resolve_user(ctx)
    client_name = _resolve_client_name(ctx)
    try:
        result = fn(user)
        # 写操作成功后让该用户的 list_* 缓存失效，避免「列表→创建→再列表」读到旧数据
        if user is not None and tool_name.startswith('confirm_'):
            _bump_list_epoch(user.id)
        _log_call(user, tool_name, arguments, started, client_name=client_name)
        return result
    except ToolError:
        raise
    except ConfirmError as e:
        _log_call(user, tool_name, arguments, started, error=str(e),
                  status='denied', client_name=client_name)
        raise ToolError(str(e)) from e
    except act.McpActionError as e:
        _log_call(user, tool_name, arguments, started, error=str(e),
                  status='error', client_name=client_name)
        raise ToolError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception('MCP 工具 %s 执行异常', tool_name)
        _log_call(user, tool_name, arguments, started, error=str(e),
                  status='error', client_name=client_name)
        raise ToolError(f'{tool_name} 执行失败: {e}') from e
    finally:
        if not connection.in_atomic_block:
            close_old_connections()


# --------------------------------------------------------------------- #
# 读工具（同步即时返回，轻量字段 + 分页 + 60s 缓存）
# --------------------------------------------------------------------- #

def list_projects(ctx: Context, limit: int = 20, offset: int = 0) -> dict:
    """列出当前用户拥有或参与的测试项目（测试用例域）。

    返回 {total, items: [{id, name, status, created_at, ui_project_id, api_project_id}]}，
    ui_project_id / api_project_id 是该统一项目对应的 UI、API 自动化项目 ID
    （未创建时为 null），可直接用于 list_ui_*/list_api_* 与 preview_create_ui_case
    等需要自动化项目 ID 的工具。支持 limit/offset 分页（limit 上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        key = f'mcp:tool:list_projects:{user.id}:{_list_epoch(user.id)}:{limit}:{offset}'

        def build():
            from apps.api_testing.models import ApiProject
            from apps.ui_automation.models import UiProject

            qs = act.accessible_projects(user).order_by('-created_at')
            total = qs.count()
            rows = list(qs[offset:offset + limit].values('id', 'name', 'status', 'created_at'))
            ids = [row['id'] for row in rows]
            ui_ids = dict(
                UiProject.objects.filter(workspace_project_id__in=ids, is_deleted=False)
                .values_list('workspace_project_id', 'id'))
            api_ids = dict(
                ApiProject.objects.filter(workspace_project_id__in=ids)
                .values_list('workspace_project_id', 'id'))
            for row in rows:
                row['ui_project_id'] = ui_ids.get(row['id'])
                row['api_project_id'] = api_ids.get(row['id'])
            return {'total': total, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_projects', {'limit': limit, 'offset': offset}, run)


def list_testcases(ctx: Context, project_id: int, module_id: int = 0,
                   include_children: bool = True, keyword: str = '',
                   linked: str = '', limit: int = 20, offset: int = 0) -> dict:
    """列出指定项目下的功能测试用例，并带回自动化关联状态。

    返回 {total, items: [{id, title, priority, status, test_type, module_id,
    created_at, automation_links}]}；automation_links 每项为
    {type: ui_test_case|api_scenario_case, object_id, target_name,
    target_exists（目标仍存在且归属同一统一项目）, accessible（当前用户可见）}。
    module_id 传 0 表示不限模块，include_children=True 时包含子模块下的用例；
    keyword 模糊匹配标题/描述；linked 过滤关联状态：
    any（有有效关联）/ none（无任何有效关联）/ ui / api（有有效关联）/
    ui_missing / api_missing（无有效关联，含悬空）/ all_missing（两类都无有效关联）/
    ui_stale / api_stale（有记录但目标已失效，需要修复）。
    需对该项目有访问权限，支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)
    linked = (linked or '').strip().lower()
    valid_linked = ('', 'any', 'none', 'ui', 'api', 'ui_missing', 'api_missing',
                    'all_missing', 'ui_stale', 'api_stale')
    if linked not in valid_linked:
        raise ToolError(
            f'linked 无效: {linked}，可选: {", ".join(v for v in valid_linked if v)}')

    def run(user):
        from django.db.models import Q
        from apps.testcases.models import TestCase, TestCaseModule
        from apps.testcases.views import get_automation_links_for_cases
        if not act.accessible_projects(user).filter(id=project_id).exists():
            raise ToolError(f'项目 {project_id} 不存在或无权限')
        key = (f'mcp:tool:list_testcases:{user.id}:{_list_epoch(user.id)}:{project_id}:{module_id}:'
               f'{int(bool(include_children))}:{keyword}:{linked}:{limit}:{offset}')

        def build():
            qs = TestCase.objects.filter(project_id=project_id).order_by('-created_at')
            if module_id:
                module_ids = [module_id]
                if include_children:
                    frontier = [module_id]
                    while frontier:
                        frontier = list(TestCaseModule.objects.filter(
                            parent_id__in=frontier).values_list('id', flat=True))
                        module_ids.extend(frontier)
                qs = qs.filter(module_id__in=module_ids)
            if keyword:
                qs = qs.filter(Q(title__icontains=keyword)
                               | Q(description__icontains=keyword))

            # 关联过滤按「目标结构有效性」判定：目标被删除、移到其他项目或
            # 所属自动化项目解绑统一项目时算作未关联，避免补齐流程漏掉待修复用例。
            ui_valid, ui_stale = _link_case_state('ui_test_case', project_id)
            api_valid, api_stale = _link_case_state('api_scenario_case', project_id)
            if linked == 'any':
                qs = qs.filter(Q(id__in=ui_valid) | Q(id__in=api_valid))
            elif linked == 'none':
                qs = qs.exclude(Q(id__in=ui_valid) | Q(id__in=api_valid))
            elif linked == 'ui':
                qs = qs.filter(id__in=ui_valid)
            elif linked == 'api':
                qs = qs.filter(id__in=api_valid)
            elif linked == 'ui_missing':
                qs = qs.exclude(id__in=ui_valid)
            elif linked == 'api_missing':
                qs = qs.exclude(id__in=api_valid)
            elif linked == 'all_missing':
                qs = qs.exclude(id__in=ui_valid).exclude(id__in=api_valid)
            elif linked == 'ui_stale':
                qs = qs.filter(id__in=ui_stale)
            elif linked == 'api_stale':
                qs = qs.filter(id__in=api_stale)

            total = qs.count()
            cases = list(qs.only('id', 'title', 'priority', 'status', 'test_type',
                                 'module_id', 'project_id', 'created_at')
                         [offset:offset + limit])
            links_by_case = _enrich_automation_links(
                get_automation_links_for_cases(cases, user), project_id)
            items = [{
                'id': case.id,
                'title': case.title,
                'priority': case.priority,
                'status': case.status,
                'test_type': case.test_type,
                'module_id': case.module_id,
                'created_at': case.created_at,
                'automation_links': links_by_case.get(case.id, []),
            } for case in cases]
            return {'total': total, 'items': items}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_testcases',
                     {'project_id': project_id, 'module_id': module_id,
                      'include_children': bool(include_children), 'keyword': keyword,
                      'linked': linked, 'limit': limit, 'offset': offset}, run)


def list_testcase_modules(ctx: Context, project_id: int) -> dict:
    """列出项目下的用例模块目录（扁平结构 + 完整路径）。

    返回 {total, unassigned_case_count, items: [{id, name, parent_id, parent_name,
    path, level, sort_order, case_count}]}；path 形如「登录/子模块」，
    level 从 0 开始，case_count 只统计直接挂在该模块下的用例。
    module_id 可直接传给 list_testcases 过滤用例。
    """
    def run(user):
        from django.db.models import Count
        from apps.testcases.models import TestCase, TestCaseModule
        if not act.accessible_projects(user).filter(id=project_id).exists():
            raise ToolError(f'项目 {project_id} 不存在或无权限')
        key = f'mcp:tool:list_testcase_modules:{user.id}:{_list_epoch(user.id)}:{project_id}'

        def build():
            modules = list(TestCaseModule.objects.filter(project_id=project_id).values(
                'id', 'name', 'parent_id', 'sort_order'))
            by_id = {module['id']: module for module in modules}
            counts = {
                row['module_id']: row['c']
                for row in TestCase.objects.filter(project_id=project_id)
                .exclude(module__isnull=True).values('module_id').annotate(c=Count('id'))
            }
            for module in modules:
                path, cursor, guard = [module['name']], module['parent_id'], set()
                while cursor and cursor in by_id and cursor not in guard:
                    guard.add(cursor)
                    path.append(by_id[cursor]['name'])
                    cursor = by_id[cursor]['parent_id']
                module['path'] = '/'.join(reversed(path))
                module['level'] = len(path) - 1
                module['parent_name'] = (by_id[module['parent_id']]['name']
                                         if module['parent_id'] in by_id else '')
                module['case_count'] = counts.get(module['id'], 0)
            unassigned = TestCase.objects.filter(
                project_id=project_id, module__isnull=True).count()
            return {'total': len(modules),
                    'unassigned_case_count': unassigned,
                    'items': modules}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_testcase_modules', {'project_id': project_id}, run)


def get_testcase(ctx: Context, testcase_id: int) -> dict:
    """获取单个功能测试用例的完整内容（含步骤、预期结果与自动化关联）。

    返回 {id, title, description, notes, preconditions,
    steps: [{id, sort_order, action, expected}], priority, status, test_type, tags,
    project_id, module_id, module_path, author, assignee, version_ids,
    automation_links: [{type, object_id, target_name, target_exists, accessible}],
    has_ui_automation, has_api_automation, created_at, updated_at}。
    步骤与预期结果一一对应，按 sort_order 排序；步骤 id 可直接回传给
    preview_update_testcase 做按主键增量更新（不带 id 的视为新增）。
    生成 UI/接口自动化用例前用它读取步骤与预期结果。
    """
    def run(user):
        from apps.testcases.models import TestCase
        from apps.testcases.views import get_automation_links_for_cases
        case = (TestCase.objects.filter(
            id=testcase_id, project__in=act.accessible_projects(user))
            .select_related('project', 'module', 'author', 'assignee').first())
        if not case:
            raise ToolError(f'功能测试用例 {testcase_id} 不存在或无权限')

        links = get_automation_links_for_cases([case], user).get(case.id, [])
        links = _enrich_automation_links({case.id: links}, case.project_id)[case.id]
        module_path = ''
        if case.module_id:
            names, cursor, guard = [], case.module, set()
            while cursor is not None and cursor.id not in guard:
                guard.add(cursor.id)
                names.append(cursor.name)
                cursor = cursor.parent
            module_path = '/'.join(reversed(names))
        return {
            'id': case.id,
            'title': case.title,
            'description': case.description,
            'notes': case.notes,
            'preconditions': case.preconditions,
            'steps': [{'id': step.id, 'sort_order': step.sort_order,
                       'action': step.action, 'expected': step.expected}
                      for step in case.step_details.all()],
            'priority': case.priority,
            'status': case.status,
            'test_type': case.test_type,
            'tags': case.tags or [],
            'project_id': case.project_id,
            'module_id': case.module_id,
            'module_path': module_path,
            'author': case.author.username if case.author else '',
            'assignee': case.assignee.username if case.assignee else '',
            'version_ids': list(case.versions.values_list('id', flat=True)),
            'automation_links': links,
            'has_ui_automation': any(l['type'] == 'ui_test_case' and l['target_exists']
                                     for l in links),
            'has_api_automation': any(l['type'] == 'api_scenario_case' and l['target_exists']
                                      for l in links),
            'created_at': case.created_at,
            'updated_at': case.updated_at,
        }

    return _run_tool(ctx, 'get_testcase', {'testcase_id': testcase_id}, run)


def list_api_requests(ctx: Context, project_id: int, keyword: str = '',
                      method: str = '', collection_id: int = 0,
                      limit: int = 20, offset: int = 0) -> dict:
    """列出指定 API 测试项目下的接口请求（可按名称/URL、方法、集合过滤）。

    返回 {total, items: [{id, name, method, url, request_type,
    collection_id, collection_name}]}，用于组装场景用例时获取 request_id。
    keyword 模糊匹配名称/URL，method 精确匹配（GET/POST/...），
    collection_id 限定集合。需对该 API 项目有访问权限，按集合与请求顺序排序，
    支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)
    method = (method or '').strip().upper()

    def run(user):
        from django.db.models import Q
        from apps.api_testing.models import ApiRequest
        if not act.accessible_api_projects(user).filter(id=project_id).exists():
            raise ToolError(f'API 项目 {project_id} 不存在或无权限')
        if method and method not in dict(ApiRequest.HTTP_METHODS):
            raise ToolError(
                f'method 无效: {method}，可选: {", ".join(dict(ApiRequest.HTTP_METHODS))}')
        key = (f'mcp:tool:list_api_requests:{user.id}:{_list_epoch(user.id)}:{project_id}:{keyword}:'
               f'{method}:{collection_id}:{limit}:{offset}')

        def build():
            qs = ApiRequest.objects.filter(
                collection__project_id=project_id
            ).select_related('collection').order_by(
                'collection__order', 'collection_id', 'order', 'id')
            if keyword:
                qs = qs.filter(Q(name__icontains=keyword) | Q(url__icontains=keyword))
            if method:
                qs = qs.filter(method=method)
            if collection_id:
                qs = qs.filter(collection_id=collection_id)
            total = qs.count()
            items = [{
                'id': api_request.id,
                'name': api_request.name,
                'method': api_request.method,
                'url': api_request.url,
                'request_type': api_request.request_type,
                'collection_id': api_request.collection_id,
                'collection_name': (api_request.collection.name
                                    if api_request.collection else ''),
            } for api_request in qs.only(
                'id', 'name', 'method', 'url', 'request_type', 'collection')
                [offset:offset + limit]]
            return {'total': total, 'items': items}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_api_requests',
                     {'project_id': project_id, 'keyword': keyword, 'method': method,
                      'collection_id': collection_id, 'limit': limit, 'offset': offset}, run)


def get_api_request(ctx: Context, request_id: int) -> dict:
    """获取单个接口请求的完整配置，用于判断能否复用到场景用例。

    list_api_requests 只返回 id/name/method/url 等摘要，无法据此判断断言、
    变量提取、认证与脚本是否可直接复用；组装场景前需要细节时调用本工具。
    返回 {id, name, description, request_type, method, url, is_write,
    collection_id, collection_name, collection_path, project_id,
    headers, params, body, auth, assertions, extractors,
    pre_request_script, post_request_script,
    has_assertions, has_extractors, referenced_by_scenarios}；
    is_write 表示方法为 POST/PUT/PATCH/DELETE（执行会产生副作用）；
    referenced_by_scenarios 为已引用该请求的场景用例数，用于评估改动影响。
    """
    def run(user):
        from apps.api_testing.models import ApiRequest, ScenarioCaseRequest
        request = (ApiRequest.objects.filter(
            id=request_id, collection__project__in=act.accessible_api_projects(user))
            .select_related('collection').first())
        if not request:
            raise ToolError(f'接口请求 {request_id} 不存在或无权限')

        collection_path = ''
        if request.collection_id:
            names, cursor, guard = [], request.collection, set()
            while cursor is not None and cursor.id not in guard:
                guard.add(cursor.id)
                names.append(cursor.name)
                cursor = cursor.parent
            collection_path = '/'.join(reversed(names))

        referenced = ScenarioCaseRequest.objects.filter(
            request_id=request.id).values('scenario_case_id').distinct().count()
        return {
            'id': request.id,
            'name': request.name,
            'description': request.description,
            'request_type': request.request_type,
            'method': request.method,
            'url': request.url,
            'is_write': request.method in ('POST', 'PUT', 'PATCH', 'DELETE'),
            'collection_id': request.collection_id,
            'collection_name': request.collection.name if request.collection else '',
            'collection_path': collection_path,
            'project_id': request.collection.project_id if request.collection else None,
            'headers': request.headers or {},
            'params': request.params or {},
            'body': request.body or {},
            'auth': request.auth or {},
            'assertions': request.assertions or [],
            'extractors': request.extractors or [],
            'pre_request_script': request.pre_request_script,
            'post_request_script': request.post_request_script,
            'has_assertions': bool(request.assertions),
            'has_extractors': bool(request.extractors),
            'referenced_by_scenarios': referenced,
        }

    return _run_tool(ctx, 'get_api_request', {'request_id': request_id}, run)


def list_api_collections(ctx: Context, project_id: int) -> dict:
    """列出 API 项目下的集合（接口目录，扁平结构 + 请求数量）。

    返回 {total, items: [{id, name, parent_id, parent_name, path, level,
    order, request_count}]}；新建接口请求时用 id 作为 collection_id，
    或直接传集合名称给 collection_name 复用/新建。
    """
    def run(user):
        from django.db.models import Count
        from apps.api_testing.models import ApiCollection, ApiRequest
        if not act.accessible_api_projects(user).filter(id=project_id).exists():
            raise ToolError(f'API 项目 {project_id} 不存在或无权限')
        key = f'mcp:tool:list_api_collections:{user.id}:{_list_epoch(user.id)}:{project_id}'

        def build():
            collections = list(ApiCollection.objects.filter(
                project_id=project_id).values('id', 'name', 'parent_id', 'order'))
            by_id = {collection['id']: collection for collection in collections}
            counts = {
                row['collection_id']: row['c']
                for row in ApiRequest.objects.filter(collection__project_id=project_id)
                .values('collection_id').annotate(c=Count('id'))
            }
            for collection in collections:
                path, cursor, guard = ([collection['name']], collection['parent_id'], set())
                while cursor and cursor in by_id and cursor not in guard:
                    guard.add(cursor)
                    path.append(by_id[cursor]['name'])
                    cursor = by_id[cursor]['parent_id']
                collection['path'] = '/'.join(reversed(path))
                collection['level'] = len(path) - 1
                collection['parent_name'] = (
                    by_id[collection['parent_id']]['name']
                    if collection['parent_id'] in by_id else '')
                collection['request_count'] = counts.get(collection['id'], 0)
            return {'total': len(collections), 'items': collections}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_api_collections', {'project_id': project_id}, run)


def list_ui_cases(ctx: Context, project_id: int, folder_id: int = 0,
                  include_children: bool = True, keyword: str = '',
                  limit: int = 20, offset: int = 0) -> dict:
    """列出指定 UI 自动化项目下的测试用例（可按目录、关键词过滤）。

    返回 {total, items: [{id, name, status, priority, folder_id, folder_name,
    folder_path, created_at}]}；folder_id 传 0 不限目录，
    include_children=True 时包含子目录下的用例，keyword 模糊匹配名称/描述。
    目录 ID 可从 list_ui_case_folders 获取。
    需对该 UI 项目有访问权限，支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        from django.db.models import Q
        from apps.ui_automation.models import TestCase as UiCase, TestCaseFolder
        if not act.accessible_ui_projects(user).filter(id=project_id).exists():
            raise ToolError(f'UI 项目 {project_id} 不存在或无权限')
        key = (f'mcp:tool:list_ui_cases:{user.id}:{_list_epoch(user.id)}:{project_id}:'
               f'{folder_id}:{int(bool(include_children))}:{keyword}:{limit}:{offset}')

        def build():
            qs = UiCase.objects.filter(project_id=project_id).order_by('-created_at')
            if folder_id:
                ids = (_folder_subtree_ids(TestCaseFolder, project_id, folder_id)
                       if include_children else [folder_id])
                qs = qs.filter(folder_id__in=ids)
            if keyword:
                qs = qs.filter(Q(name__icontains=keyword)
                               | Q(description__icontains=keyword))
            total = qs.count()
            rows = list(qs[offset:offset + limit].values(
                'id', 'name', 'status', 'priority', 'folder_id', 'created_at'))
            index = _folder_index(TestCaseFolder, project_id)
            for row in rows:
                info = index.get(row['folder_id']) or {}
                row['folder_name'] = info.get('name', '')
                row['folder_path'] = info.get('path', '')
            return {'total': total, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_ui_cases',
                     {'project_id': project_id, 'folder_id': folder_id,
                      'include_children': bool(include_children), 'keyword': keyword,
                      'limit': limit, 'offset': offset}, run)


def list_ui_case_folders(ctx: Context, project_id: int) -> dict:
    """列出 UI 项目下的用例目录（扁平结构 + 完整路径与用例数）。

    返回 {total, unassigned_case_count, items: [{id, name, parent_id, parent_name,
    path, level, case_count}]}，path 形如「登录/短信登录」，case_count 只统计
    直接挂在该目录的用例，unassigned_case_count 为未归目录的用例数。
    拿到的 id 可直接传给 list_ui_cases 的 folder_id。
    """
    def run(user):
        from django.db.models import Count
        from apps.ui_automation.models import TestCase as UiCase, TestCaseFolder
        if not act.accessible_ui_projects(user).filter(id=project_id).exists():
            raise ToolError(f'UI 项目 {project_id} 不存在或无权限')
        key = f'mcp:tool:list_ui_case_folders:{user.id}:{_list_epoch(user.id)}:{project_id}'

        def build():
            index = _folder_index(TestCaseFolder, project_id)
            counts = {
                row['folder_id']: row['c']
                for row in UiCase.objects.filter(project_id=project_id)
                .exclude(folder__isnull=True)
                .values('folder_id').annotate(c=Count('id'))
            }
            items = [{
                'id': folder_id,
                'name': info['name'],
                'parent_id': info['parent_id'],
                'parent_name': (index[info['parent_id']]['name']
                                if info['parent_id'] in index else ''),
                'path': info['path'],
                'level': info['level'],
                'case_count': counts.get(folder_id, 0),
            } for folder_id, info in index.items()]
            items.sort(key=lambda item: item['path'])
            unassigned = UiCase.objects.filter(
                project_id=project_id, folder__isnull=True).count()
            return {'total': len(items), 'unassigned_case_count': unassigned,
                    'items': items}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_ui_case_folders', {'project_id': project_id}, run)


def list_api_scenarios(ctx: Context, project_id: int, folder_id: int = 0,
                       include_children: bool = True, keyword: str = '',
                       limit: int = 20, offset: int = 0) -> dict:
    """列出指定 API 测试项目下的场景用例（接口编排单元，可按目录过滤）。

    返回 {total, items: [{id, name, description, folder_id, folder_name,
    folder_path, environment_id, request_count, created_at}]}；
    folder_id 传 0 不限目录，include_children=True 时包含子目录下的场景，
    keyword 模糊匹配名称/描述。目录 ID 可从 list_scenario_case_folders 获取。
    执行场景用例请使用 preview_run_api_scenario，需对该 API 项目有访问权限，
    支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        from django.db.models import Count, Q
        from apps.api_testing.models import ScenarioCase, ScenarioCaseFolder
        if not act.accessible_api_projects(user).filter(id=project_id).exists():
            raise ToolError(f'API 项目 {project_id} 不存在或无权限')
        key = (f'mcp:tool:list_api_scenarios:{user.id}:{_list_epoch(user.id)}:{project_id}:'
               f'{folder_id}:{int(bool(include_children))}:{keyword}:{limit}:{offset}')

        def build():
            qs = ScenarioCase.objects.filter(project_id=project_id).annotate(
                request_count=Count('scenariocaserequest')).order_by('-created_at')
            if folder_id:
                ids = (_folder_subtree_ids(ScenarioCaseFolder, project_id, folder_id)
                       if include_children else [folder_id])
                qs = qs.filter(folder_id__in=ids)
            if keyword:
                qs = qs.filter(Q(name__icontains=keyword)
                               | Q(description__icontains=keyword))
            total = qs.count()
            rows = list(qs[offset:offset + limit].values(
                'id', 'name', 'description', 'folder_id', 'environment_id',
                'request_count', 'created_at'))
            index = _folder_index(ScenarioCaseFolder, project_id)
            for row in rows:
                info = index.get(row['folder_id']) or {}
                row['folder_name'] = info.get('name', '')
                row['folder_path'] = info.get('path', '')
            return {'total': total, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_api_scenarios',
                     {'project_id': project_id, 'folder_id': folder_id,
                      'include_children': bool(include_children), 'keyword': keyword,
                      'limit': limit, 'offset': offset}, run)


def list_scenario_case_folders(ctx: Context, project_id: int) -> dict:
    """列出 API 项目下的场景用例目录（扁平结构 + 完整路径与场景数）。

    返回 {total, unassigned_case_count, items: [{id, name, parent_id, parent_name,
    path, level, case_count}]}，path 形如「下单/正向流程」，case_count 只统计
    直接挂在该目录的场景用例，unassigned_case_count 为未归目录的场景数。
    拿到的 id 可直接传给 list_api_scenarios 的 folder_id。
    """
    def run(user):
        from django.db.models import Count
        from apps.api_testing.models import ScenarioCase, ScenarioCaseFolder
        if not act.accessible_api_projects(user).filter(id=project_id).exists():
            raise ToolError(f'API 项目 {project_id} 不存在或无权限')
        key = (f'mcp:tool:list_scenario_case_folders:{user.id}:'
               f'{_list_epoch(user.id)}:{project_id}')

        def build():
            index = _folder_index(ScenarioCaseFolder, project_id)
            counts = {
                row['folder_id']: row['c']
                for row in ScenarioCase.objects.filter(project_id=project_id)
                .exclude(folder__isnull=True)
                .values('folder_id').annotate(c=Count('id'))
            }
            items = [{
                'id': folder_id,
                'name': info['name'],
                'parent_id': info['parent_id'],
                'parent_name': (index[info['parent_id']]['name']
                                if info['parent_id'] in index else ''),
                'path': info['path'],
                'level': info['level'],
                'case_count': counts.get(folder_id, 0),
            } for folder_id, info in index.items()]
            items.sort(key=lambda item: item['path'])
            unassigned = ScenarioCase.objects.filter(
                project_id=project_id, folder__isnull=True).count()
            return {'total': len(items), 'unassigned_case_count': unassigned,
                    'items': items}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_scenario_case_folders', {'project_id': project_id}, run)


def list_api_suites(ctx: Context, project_id: int, limit: int = 20, offset: int = 0) -> dict:
    """列出指定 API 测试项目下的测试套件（场景用例的固定回归集）。

    返回 {total, items: [{id, name, description, environment_id,
    scenario_count, created_at}]}。执行套件请使用 preview_run_api_suite，
    需对该 API 项目有访问权限，支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        from django.db.models import Count
        from apps.api_testing.models import TestSuite
        if not act.accessible_api_projects(user).filter(id=project_id).exists():
            raise ToolError(f'API 项目 {project_id} 不存在或无权限')
        key = f'mcp:tool:list_api_suites:{user.id}:{_list_epoch(user.id)}:{project_id}:{limit}:{offset}'

        def build():
            qs = TestSuite.objects.filter(project_id=project_id).annotate(
                scenario_count=Count('testsuiteitem')).order_by('-created_at')
            total = qs.count()
            rows = list(qs[offset:offset + limit].values(
                'id', 'name', 'description', 'environment_id', 'scenario_count', 'created_at'))
            return {'total': total, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_api_suites',
                     {'project_id': project_id, 'limit': limit, 'offset': offset}, run)


def list_ui_suites(ctx: Context, project_id: int, limit: int = 20, offset: int = 0) -> dict:
    """列出指定 UI 自动化项目下的测试套件。

    返回 {total, items: [{id, name, description, execution_status,
    passed_count, failed_count, case_count, created_at}]}。
    执行套件请使用 preview_run_ui_suite，需对该 UI 项目有访问权限，
    支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        from django.db.models import Count
        from apps.ui_automation.models import TestSuite as UiSuite
        if not act.accessible_ui_projects(user).filter(id=project_id).exists():
            raise ToolError(f'UI 项目 {project_id} 不存在或无权限')
        key = f'mcp:tool:list_ui_suites:{user.id}:{_list_epoch(user.id)}:{project_id}:{limit}:{offset}'

        def build():
            qs = UiSuite.objects.filter(project_id=project_id).annotate(
                case_count=Count('suite_test_cases')).order_by('-created_at')
            total = qs.count()
            rows = list(qs[offset:offset + limit].values(
                'id', 'name', 'description', 'execution_status',
                'passed_count', 'failed_count', 'case_count', 'created_at'))
            return {'total': total, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_ui_suites',
                     {'project_id': project_id, 'limit': limit, 'offset': offset}, run)


def list_ui_elements(ctx: Context, project_id: int, page: str = '', group_id: int = 0,
                     keyword: str = '', limit: int = 20, offset: int = 0) -> dict:
    """获取 UI 元素库：页面模块（分组）索引 + 元素列表。

    返回 {total, page_modules, items}：
    - page_modules: [{id, name, parent_id, element_count, source}]，source=group 表示
      元素分组（页面模块），source=page 表示未归组元素的 page 字段聚合
    - items: [{id, name, element_type, page, group_id, group_name,
      locator_strategy, locator_value, locator_options, validation_status, usage_count}]

    page 按页面名精确过滤，group_id 按元素分组过滤，keyword 模糊匹配名称/描述/页面；
    分页作用于元素列表，page_modules 始终返回全量索引。需对该 UI 项目有访问权限。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        from django.db.models import Count
        from apps.ui_automation.models import Element, ElementGroup
        if not act.accessible_ui_projects(user).filter(id=project_id).exists():
            raise ToolError(f'UI 项目 {project_id} 不存在或无权限')

        key = (f'mcp:tool:list_ui_elements:{user.id}:{_list_epoch(user.id)}:{project_id}:'
               f'{page}:{group_id}:{keyword}:{limit}:{offset}')

        def build():
            base = Element.objects.filter(project_id=project_id)

            groups = list(ElementGroup.objects.filter(project_id=project_id).values(
                'id', 'name', 'parent_group_id'))
            counts = {
                row['group_id']: row['c']
                for row in base.exclude(group__isnull=True)
                .values('group_id').annotate(c=Count('id'))
            }
            page_modules = [{
                'id': group['id'],
                'name': group['name'],
                'parent_id': group['parent_group_id'],
                'element_count': counts.get(group['id'], 0),
                'source': 'group',
            } for group in groups]

            # 未归组元素按 page 字段聚合，保证元素库覆盖全部元素
            page_counts = {
                row['page']: row['c']
                for row in base.exclude(page='').filter(group__isnull=True)
                .values('page').annotate(c=Count('id'))
            }
            for page_name in sorted(page_counts):
                page_modules.append({
                    'id': None,
                    'name': page_name,
                    'parent_id': None,
                    'element_count': page_counts[page_name],
                    'source': 'page',
                })

            qs = base.select_related('group', 'locator_strategy')
            if page:
                qs = qs.filter(page=page)
            if group_id:
                qs = qs.filter(group_id=group_id)
            if keyword:
                from django.db.models import Q
                qs = qs.filter(Q(name__icontains=keyword) | Q(description__icontains=keyword)
                               | Q(page__icontains=keyword))
            qs = qs.order_by('page', 'order', 'name')
            total = qs.count()
            rows = []
            for element in qs[offset:offset + limit]:
                rows.append({
                    'id': element.id,
                    'name': element.name,
                    'element_type': element.element_type,
                    'page': element.page,
                    'group_id': element.group_id,
                    'group_name': element.group.name if element.group else '',
                    'locator_strategy': (element.locator_strategy.name
                                         if element.locator_strategy else ''),
                    'locator_value': element.locator_value,
                    'locator_options': element.locator_options or {},
                    'backup_locators': element.backup_locators or [],
                    'validation_status': element.validation_status,
                    'usage_count': element.usage_count,
                    'description': element.description,
                })
            return {'total': total, 'page_modules': page_modules, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_ui_elements',
                     {'project_id': project_id, 'page': page, 'group_id': group_id,
                      'keyword': keyword, 'limit': limit, 'offset': offset}, run)


def list_perf_scenes(ctx: Context, project_id: int, limit: int = 20, offset: int = 0) -> dict:
    """列出指定压测项目下的压测场景。

    返回 {total, items: [{id, name, engine, enabled, created_at}]}，
    需对该压测项目有访问权限，支持 limit/offset 分页（上限 100），60 秒缓存。
    """
    limit, offset = _clamp_page(limit, offset)

    def run(user):
        from apps.perf_testing.models import PerfScenario
        if not act.accessible_perf_projects(user).filter(id=project_id).exists():
            raise ToolError(f'压测项目 {project_id} 不存在或无权限')
        key = f'mcp:tool:list_perf_scenes:{user.id}:{_list_epoch(user.id)}:{project_id}:{limit}:{offset}'

        def build():
            qs = PerfScenario.objects.filter(project_id=project_id).order_by('-created_at')
            total = qs.count()
            rows = list(qs.only('id', 'name', 'engine', 'enabled', 'created_at')
                        [offset:offset + limit].values('id', 'name', 'engine', 'enabled', 'created_at'))
            return {'total': total, 'items': rows}
        return _cache_get_or_set(key, build)

    return _run_tool(ctx, 'list_perf_scenes',
                     {'project_id': project_id, 'limit': limit, 'offset': offset}, run)


def get_report(ctx: Context, execution_id: int) -> dict:
    """获取指定压测执行的报告摘要（复用执行落库指标，不重新计算）。

    返回状态、SLA 结果、结论（verdict）及明细、汇总指标、时长，
    以及按请求维度的统计（总量/失败/错误率/平均响应/P95/TPS）。
    需对该压测执行所属项目有访问权限。
    """

    def run(user):
        from apps.perf_testing.models import PerfExecution, PerfRequestStat
        execution = PerfExecution.objects.filter(
            id=execution_id, project__in=act.accessible_perf_projects(user)
        ).first()
        if not execution:
            raise ToolError(f'压测执行 {execution_id} 不存在或无权限')
        stats = list(PerfRequestStat.objects.filter(execution_id=execution.id).values(
            'step_name', 'total', 'failed', 'error_rate',
            'avg_response_time', 'p95', 'tps'))
        return {
            'execution_id': execution.id,
            'execution_no': execution.execution_no,
            'status': execution.status,
            'sla_result': execution.sla_result,
            'verdict': execution.verdict,
            'verdict_details': execution.verdict_details or [],
            'summary': execution.summary or {},
            'duration': execution.duration,
            'report_url': execution.report_url or '',
            'stats': stats,
        }

    return _run_tool(ctx, 'get_report', {'execution_id': execution_id}, run)


def get_api_scenario_execution(ctx: Context, execution_id: int) -> dict:
    """查询接口场景用例执行结果（preview_run_api_scenario 返回执行 ID 后轮询）。

    返回状态（PENDING/RUNNING/COMPLETED/FAILED）、请求总数/通过数/失败数、
    逐请求结果（名称、方法、URL、状态码、响应耗时、断言明细）与耗时。
    状态为 PENDING/RUNNING 时请间隔数秒后再次调用。需对场景用例所属 API 项目有权限。
    """

    def run(user):
        from apps.api_testing.models import TestExecution
        execution = TestExecution.objects.filter(
            id=execution_id, scenario_case__project__in=act.accessible_api_projects(user)
        ).select_related('scenario_case').first()
        if not execution:
            raise ToolError(f'接口场景执行 {execution_id} 不存在或无权限')

        results = execution.results or []
        if not isinstance(results, list):
            results = []
        duration = None
        if execution.start_time and execution.end_time:
            duration = round((execution.end_time - execution.start_time).total_seconds(), 2)

        payload = {
            'execution_id': execution.id,
            'scenario_case_id': execution.scenario_case_id,
            'scenario_case_name': execution.scenario_case.name if execution.scenario_case else '',
            'project_id': execution.scenario_case.project_id if execution.scenario_case else None,
            'status': execution.status,
            'total_requests': execution.total_requests,
            'passed_requests': execution.passed_requests,
            'failed_requests': execution.failed_requests,
            'start_time': execution.start_time,
            'end_time': execution.end_time,
            'duration': duration,
            'results': results,
        }
        if execution.status in ('PENDING', 'RUNNING'):
            payload['hint'] = '执行尚未结束，请数秒后再次调用本工具'
        return payload

    return _run_tool(ctx, 'get_api_scenario_execution', {'execution_id': execution_id}, run)


def get_api_suite_execution(ctx: Context, execution_id: int) -> dict:
    """查询接口测试套件执行结果（preview_run_api_suite 返回执行 ID 后轮询）。

    返回套件执行状态、场景用例总数/通过数/失败数、每个场景用例的结果
    （含各自的执行 ID，可再用 get_api_scenario_execution 查请求级明细）、
    耗时与错误信息。状态为 PENDING/RUNNING 时请间隔数秒后再次调用。
    """

    def run(user):
        from apps.api_testing.models import ApiSuiteExecution
        execution = ApiSuiteExecution.objects.filter(
            id=execution_id, test_suite__project__in=act.accessible_api_projects(user)
        ).select_related('test_suite').first()
        if not execution:
            raise ToolError(f'接口套件执行 {execution_id} 不存在或无权限')

        payload = {
            'execution_id': execution.id,
            'suite_id': execution.test_suite_id,
            'suite_name': execution.test_suite.name if execution.test_suite else '',
            'project_id': execution.test_suite.project_id if execution.test_suite else None,
            'status': execution.status,
            'total_scenarios': execution.total_scenarios,
            'passed_scenarios': execution.passed_scenarios,
            'failed_scenarios': execution.failed_scenarios,
            'start_time': execution.start_time,
            'end_time': execution.end_time,
            'duration': execution.duration,
            'error_message': execution.error_message or '',
            'results': execution.results or [],
        }
        if execution.status in ('PENDING', 'RUNNING'):
            payload['hint'] = '套件执行尚未结束，请数秒后再次调用本工具'
        return payload

    return _run_tool(ctx, 'get_api_suite_execution', {'execution_id': execution_id}, run)


def get_ui_suite_execution(ctx: Context, execution_id: int) -> dict:
    """查询 UI 自动化套件执行结果（preview_run_ui_suite 返回执行 ID 后轮询）。

    返回套件执行状态（PENDING/RUNNING/SUCCESS/FAILED/ABORTED）、用例总数/
    通过数/失败数/跳过数、通过率、耗时、错误信息，以及：
    - cases：逐用例结果，字段为 test_case_id / test_case_name / status /
      steps / error / start_time / end_time / screenshots；
    - case_executions：逐用例执行记录（id / test_case_id / test_case__name /
      status / execution_time / error_message），按本次套件执行 ID 关联
      （并发执行同一套件不会串批；历史数据退回时间窗关联）。
    状态为 PENDING/RUNNING 时请间隔数秒后再次调用。
    """

    def run(user):
        from apps.ui_automation.models import TestCaseExecution, TestExecution as UiExecution
        execution = UiExecution.objects.filter(
            id=execution_id, project__in=act.accessible_ui_projects(user)
        ).select_related('test_suite').first()
        if not execution:
            raise ToolError(f'UI 套件执行 {execution_id} 不存在或无权限')

        result_data = execution.result_data or {}
        cases = []
        for item in (result_data.get('test_cases') or []):
            if isinstance(item, dict):
                cases.append(item)

        # 逐用例执行记录（TestExecutor 为每个用例创建 TestCaseExecution）；
        # 优先按 suite_execution 外键关联，避免并发执行同一套件时按时间窗串批；
        # 历史数据没有该外键，退回「套件 + 时间窗」近似关联。
        case_executions = []
        if execution.test_suite_id:
            queryset = TestCaseExecution.objects.filter(
                suite_execution_id=execution.id)
            if not queryset.exists():
                filters = {
                    'test_suite_id': execution.test_suite_id,
                    'created_at__gte': execution.started_at or execution.created_at,
                }
                if execution.finished_at:
                    filters['created_at__lte'] = execution.finished_at
                queryset = TestCaseExecution.objects.filter(**filters)
            case_executions = list(
                queryset.select_related('test_case').order_by('created_at').values(
                    'id', 'test_case_id', 'test_case__name', 'status',
                    'execution_time', 'error_message')
            )

        payload = {
            'execution_id': execution.id,
            'suite_id': execution.test_suite_id,
            'suite_name': execution.test_suite.name if execution.test_suite else '',
            'project_id': execution.project_id,
            'status': execution.status,
            'engine': execution.engine,
            'browser': execution.browser,
            'headless': execution.headless,
            'total_cases': execution.total_cases,
            'passed_cases': execution.passed_cases,
            'failed_cases': execution.failed_cases,
            'skipped_cases': execution.skipped_cases,
            'pass_rate': execution.pass_rate,
            'duration': execution.duration,
            'started_at': execution.started_at,
            'finished_at': execution.finished_at,
            'error_message': execution.error_message or '',
            'cases': cases,
            'case_executions': case_executions,
        }
        if execution.status in ('PENDING', 'RUNNING'):
            payload['hint'] = '套件执行尚未结束，请数秒后再次调用本工具'
        return payload

    return _run_tool(ctx, 'get_ui_suite_execution', {'execution_id': execution_id}, run)


def analyze_failure(ctx: Context, execution_id: int) -> dict:
    """压测失败 AI 分析：命中缓存即时返回，否则触发 Django-Q2 异步分析。

    命中缓存返回 {status: 'completed', ...分析结果}；未命中时投递异步任务
    返回 {status: 'accepted', hint}，约 30 秒后再次调用本工具获取结果。
    队列不可用时报错并提示改用平台页面 SSE 分析入口。
    """

    def run(user):
        from apps.perf_testing.models import PerfExecution
        execution = PerfExecution.objects.filter(
            id=execution_id, project__in=act.accessible_perf_projects(user)
        ).first()
        if not execution:
            raise ToolError(f'压测执行 {execution_id} 不存在或无权限')

        cache_key = f'perf:ai_analysis:{execution.id}'
        cached = cache.get(cache_key)
        if cached:
            try:
                data = json.loads(cached)
                return {'status': 'completed', 'execution_id': execution.id, **data}
            except (TypeError, ValueError):
                pass

        from apps.perf_testing.tasks import analyze_perf_execution_task
        try:
            from apps.core.task_queue import enqueue_task
            enqueue_task(analyze_perf_execution_task, execution.id)
        except Exception as e:  # noqa: BLE001 - 队列不可用时给出明确指引
            raise ToolError(
                f'AI 分析任务投递失败（任务队列不可用: {e}），'
                f'可稍后重试或使用平台页面 SSE 分析入口') from e
        return {
            'status': 'accepted',
            'execution_id': execution.id,
            'hint': '分析已异步启动（约 30s），稍后再次调用本工具获取结果',
        }

    return _run_tool(ctx, 'analyze_failure', {'execution_id': execution_id}, run)


# --------------------------------------------------------------------- #
# 危险工具：preview 同步返回预览 + confirm_token；confirm 校验后异步执行
# --------------------------------------------------------------------- #

def preview_run_api_suite(ctx: Context, suite_id: int, environment_id: int = 0) -> dict:
    """预览执行 API 测试套件（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不执行任何套件。
    可选 environment_id 覆盖套件自带执行环境；
    确认执行请调用 confirm_run_api_suite；开启人工审批时还需轮询 get_approval_status。
    """
    arguments = {'suite_id': suite_id, 'environment_id': environment_id or None}
    return _run_tool(ctx, 'preview_run_api_suite', arguments,
                     lambda user: create_preview('confirm_run_api_suite', arguments, user))


def confirm_run_api_suite(ctx: Context, confirm_token: str) -> dict:
    """确认执行 API 测试套件（危险操作第二步）。

    校验 preview_run_api_suite 返回的 confirm_token 后异步执行套件，返回执行 ID。
    令牌一次性消费且 5 分钟内有效；人工审批模式下返回 awaiting_approval，
    需轮询 get_approval_status 直至 approved/rejected/expired。
    """
    return _run_tool(ctx, 'confirm_run_api_suite', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_run_ui_case(ctx: Context, case_id: int, engine: str = 'playwright',
                        browser: str = 'chrome', headless: bool = True) -> dict:
    """预览执行 UI 自动化用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不执行任何用例。
    可选执行引擎（playwright/selenium）、浏览器与 headless 模式；
    确认执行请调用 confirm_run_ui_case。
    """
    arguments = {'case_id': case_id, 'engine': engine, 'browser': browser, 'headless': headless}
    return _run_tool(ctx, 'preview_run_ui_case', arguments,
                     lambda user: create_preview('confirm_run_ui_case', arguments, user))


def confirm_run_ui_case(ctx: Context, confirm_token: str) -> dict:
    """确认执行 UI 自动化用例（危险操作第二步）。

    校验 preview_run_ui_case 返回的 confirm_token 后异步执行用例，返回执行 ID。
    令牌一次性消费且 5 分钟内有效；人工审批模式下返回 awaiting_approval，
    需轮询 get_approval_status。
    """
    return _run_tool(ctx, 'confirm_run_ui_case', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_run_perf_scene(ctx: Context, scene_id: int) -> dict:
    """预览发起压测（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不产生任何压测流量；
    确认执行请调用 confirm_run_perf_scene。
    """
    arguments = {'scene_id': scene_id}
    return _run_tool(ctx, 'preview_run_perf_scene', arguments,
                     lambda user: create_preview('confirm_run_perf_scene', arguments, user))


def confirm_run_perf_scene(ctx: Context, confirm_token: str) -> dict:
    """确认发起压测（危险操作第二步）。

    校验 preview_run_perf_scene 返回的 confirm_token 后异步发起压测，返回执行 ID。
    令牌一次性消费且 5 分钟内有效；人工审批模式下返回 awaiting_approval，
    需轮询 get_approval_status。
    """
    return _run_tool(ctx, 'confirm_run_perf_scene', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_run_api_scenario(ctx: Context, scenario_case_id: int,
                             environment_id: int = 0) -> dict:
    """预览执行接口场景用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不发起任何 HTTP 请求。
    可选 environment_id 覆盖场景用例自带环境；确认执行请调用 confirm_run_api_scenario。
    """
    arguments = {'scenario_case_id': scenario_case_id, 'environment_id': environment_id or None}
    return _run_tool(ctx, 'preview_run_api_scenario', arguments,
                     lambda user: create_preview('confirm_run_api_scenario', arguments, user))


def confirm_run_api_scenario(ctx: Context, confirm_token: str) -> dict:
    """确认执行接口场景用例（危险操作第二步）。

    校验令牌后异步执行场景用例，返回 execution_id；请用 get_api_scenario_execution
    轮询请求级结果。人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。
    """
    return _run_tool(ctx, 'confirm_run_api_scenario', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_run_ui_suite(ctx: Context, suite_id: int, engine: str = 'playwright',
                         browser: str = 'chrome', headless: bool = True,
                         storage_state_id: int = 0) -> dict:
    """预览执行 UI 自动化套件（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不启动浏览器。
    可选引擎（playwright/selenium）、浏览器、headless 与登录态（storage_state_id）；
    确认执行请调用 confirm_run_ui_suite。
    """
    arguments = {'suite_id': suite_id, 'engine': engine, 'browser': browser,
                 'headless': headless, 'storage_state_id': storage_state_id or None}
    return _run_tool(ctx, 'preview_run_ui_suite', arguments,
                     lambda user: create_preview('confirm_run_ui_suite', arguments, user))


def confirm_run_ui_suite(ctx: Context, confirm_token: str) -> dict:
    """确认执行 UI 自动化套件（危险操作第二步）。

    校验令牌后异步执行套件，返回 execution_id；请用 get_ui_suite_execution 轮询结果。
    人工审批模式下返回 awaiting_approval，需轮询 get_approval_status。
    """
    return _run_tool(ctx, 'confirm_run_ui_suite', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_create_ui_case(ctx: Context, project_id: int, data: dict,
                           test_case_id: int = 0, replace_link: bool = False) -> dict:
    """预览新建 UI 自动化用例并关联功能用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不落库。
    data 结构：{name, description?, folder_id? 或 folder_path?（「父目录/子目录」）,
    create_missing?（末级目录不存在时是否自动创建，默认 false）,
    priority?（high/medium/low，或中文 高/中/低）,
    status?（draft/ready/running/passed/failed）,
    steps?: [{action_type, element_id?, input_value?, action_params?,
    wait_time?, assert_type?, assert_value?, description?}]}；
    test_case_id 传入功能测试用例 ID 时，创建后自动建立 UI 自动化关联；
    该功能用例已存在同类型关联时确认会被拒绝，确需替换须显式传 replace_link=true。
    """
    arguments = {'project_id': project_id, 'data': data or {},
                 'test_case_id': test_case_id or None,
                 'replace_link': bool(replace_link)}
    return _run_tool(ctx, 'preview_create_ui_case', arguments,
                     lambda user: create_preview('confirm_create_ui_case', arguments, user))


def confirm_create_ui_case(ctx: Context, confirm_token: str) -> dict:
    """确认新建 UI 自动化用例（危险操作第二步），返回 case_id 与关联信息。"""
    return _run_tool(ctx, 'confirm_create_ui_case', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_create_api_scenario_case(ctx: Context, project_id: int, data: dict,
                                     test_case_id: int = 0,
                                     replace_link: bool = False) -> dict:
    """预览新建接口场景用例并关联功能用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不落库。
    data 结构：{name, description?, folder_id? 或 folder_path?（「父目录/子目录」）,
    create_missing?（末级目录不存在时是否自动创建，默认 false）, environment_id?,
    requests?: [{request_id, order?, enabled?, assertions?, extractors?}]}；
    requests 至少需要 1 个启用请求（enabled 默认 true），否则确认会被拒绝；
    未提供 assertions/extractors 时沿用接口自身配置；
    test_case_id 传入功能测试用例 ID 时，创建后自动建立接口场景关联；
    该功能用例已存在同类型关联时确认会被拒绝，确需替换须显式传 replace_link=true。
    """
    arguments = {'project_id': project_id, 'data': data or {},
                 'test_case_id': test_case_id or None,
                 'replace_link': bool(replace_link)}
    return _run_tool(ctx, 'preview_create_api_scenario_case', arguments,
                     lambda user: create_preview('confirm_create_api_scenario_case', arguments, user))


def confirm_create_api_scenario_case(ctx: Context, confirm_token: str) -> dict:
    """确认新建接口场景用例（危险操作第二步），返回 scenario_case_id 与关联信息。"""
    return _run_tool(ctx, 'confirm_create_api_scenario_case', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_create_ui_element(ctx: Context, project_id: int, data: dict) -> dict:
    """预览新建 UI 元素（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不落库。
    data：{name, element_type?（INPUT/BUTTON/LINK/DROPDOWN/CHECKBOX/RADIO/TEXT/
    IMAGE/CONTAINER/TABLE/FORM/MODAL，默认 BUTTON）,
    page?（页面名，未传 group_id/group_path 时按页面名自动挂同名分组）,
    group_id? 或 group_path?（「父分组/子分组」，只知名称时用路径，优先于 page 的启发式）,
    create_missing?（末级分组不存在时是否自动创建，默认 false）,
    locator_strategy（策略 ID 或名称，如 CSS/XPath/text）,
    locator_value, locator_options?, backup_locators?: [{strategy, value}],
    description?, component_name?, wait_timeout?, order?}；
    同项目内定位策略 + 定位值 + 定位选项完全相同的元素会直接复用（返回 reused=true）。
    先用 list_ui_elements 查已有元素，缺失时再调用本工具。
    """
    arguments = {'project_id': project_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_create_ui_element', arguments,
                     lambda user: create_preview('confirm_create_ui_element', arguments, user))


def confirm_create_ui_element(ctx: Context, confirm_token: str) -> dict:
    """确认新建 UI 元素（危险操作第二步），返回 element_id 与 reused 标记。"""
    return _run_tool(ctx, 'confirm_create_ui_element', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_create_api_request(ctx: Context, project_id: int, data: dict) -> dict:
    """预览新建接口请求（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不落库。
    data：{name, method?（默认 GET）, url,
    collection_id? 或 collection_path?（「父集合/子集合」）或 collection_name?
    （顶层名称，不存在时自动新建该集合；collection_path 末级不存在时需 create_missing=true）,
    create_missing?（末级集合不存在时是否自动创建，默认 false）,
    description?, request_type?,
    headers?, params?, body?, auth?, assertions?, extractors?,
    pre_request_script?, post_request_script?, order?}；
    先用 list_api_collections / list_api_requests 查已有集合与接口，缺失时再调用本工具。
    """
    arguments = {'project_id': project_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_create_api_request', arguments,
                     lambda user: create_preview('confirm_create_api_request', arguments, user))


def confirm_create_api_request(ctx: Context, confirm_token: str) -> dict:
    """确认新建接口请求（危险操作第二步），返回 request_id 与集合信息。"""
    return _run_tool(ctx, 'confirm_create_api_request', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_update_testcase(ctx: Context, testcase_id: int, data: dict) -> dict:
    """预览更新功能测试用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不修改数据。
    data 为需要更新的字段集合：{title?, description?, notes?, preconditions?,
    steps?: [{id?, sort_order?, action, expected}]（按主键增量同步：带 id 的原地更新、
    不带 id 的新建、未提交的删除；数组顺序即展示顺序；单条步骤的 action 与 expected
    可以只填一项）,
    priority?（low/medium/high/critical，或中文 低/中/高/紧急）, status?（active/deprecated）, test_type?, tags?,
    module_id? 或 module_path?（「父模块/子模块」）, create_missing?,
    assignee_id?, version_ids?}。
    """
    arguments = {'testcase_id': testcase_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_update_testcase', arguments,
                     lambda user: create_preview('confirm_update_testcase', arguments, user))


def confirm_update_testcase(ctx: Context, confirm_token: str) -> dict:
    """确认更新功能测试用例（危险操作第二步），返回实际变更的字段列表。"""
    return _run_tool(ctx, 'confirm_update_testcase', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_update_ui_case(ctx: Context, case_id: int, data: dict) -> dict:
    """预览更新 UI 自动化用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不修改数据。
    data：{name?, description?, folder_id? 或 folder_path?（「父目录/子目录」）,
    create_missing?, priority?（high/medium/low，或中文 高/中/低）, status?,
    steps?}；steps 传入时会整体覆盖原步骤（元素必须属于用例所在项目）。
    """
    arguments = {'case_id': case_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_update_ui_case', arguments,
                     lambda user: create_preview('confirm_update_ui_case', arguments, user))


def confirm_update_ui_case(ctx: Context, confirm_token: str) -> dict:
    """确认更新 UI 自动化用例（危险操作第二步），返回变更字段与覆盖的步骤数。"""
    return _run_tool(ctx, 'confirm_update_ui_case', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_update_api_scenario_case(ctx: Context, scenario_case_id: int, data: dict) -> dict:
    """预览更新接口场景用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不修改数据。
    data：{name?, description?, folder_id? 或 folder_path?（「父目录/子目录」）,
    create_missing?, environment_id?, requests?}；
    requests 传入时会整体覆盖原请求编排。
    """
    arguments = {'scenario_case_id': scenario_case_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_update_api_scenario_case', arguments,
                     lambda user: create_preview('confirm_update_api_scenario_case',
                                                 arguments, user))


def confirm_update_api_scenario_case(ctx: Context, confirm_token: str) -> dict:
    """确认更新接口场景用例（危险操作第二步），返回变更字段与覆盖的请求数。"""
    return _run_tool(ctx, 'confirm_update_api_scenario_case', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_update_ui_element(ctx: Context, element_id: int, data: dict) -> dict:
    """预览更新 UI 元素（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不修改数据。
    data：{name?, description?, page?, component_name?, element_type?,
    locator_strategy?（策略 ID 或名称，如 CSS/XPath/text）, locator_value?,
    locator_options?, backup_locators?, group_id?, parent_element_id?,
    wait_timeout?, order?, is_visible?, is_enabled?, force_action?}
    """
    arguments = {'element_id': element_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_update_ui_element', arguments,
                     lambda user: create_preview('confirm_update_ui_element', arguments, user))


def confirm_update_ui_element(ctx: Context, confirm_token: str) -> dict:
    """确认更新 UI 元素（危险操作第二步），返回变更字段与最新定位信息。"""
    return _run_tool(ctx, 'confirm_update_ui_element', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def preview_create_testcase(ctx: Context, project_id: int, data: dict) -> dict:
    """预览创建功能测试用例（危险操作第一步）。

    生成影响预览并返回 confirm_token（5 分钟有效），本步骤不落库；
    确认创建请调用 confirm_create_testcase。

    data 结构：{title, description?, notes?, preconditions?,
    steps: [{action, expected}]（必填，一条步骤对应一条预期结果，数组顺序即展示顺序；
    priority?（low/medium/high/critical，或中文 低/中/高/紧急）, status?（active/deprecated，默认 active）, test_type?,
    tags?: [str], module_id? 或 module_path?（「父模块/子模块」，只知名称时用路径）,
    create_missing?（末级模块不存在时是否自动创建，默认 false）, assignee_id?,
    version_ids?: [int]}
    """
    arguments = {'project_id': project_id, 'data': data or {}}
    return _run_tool(ctx, 'preview_create_testcase', arguments,
                     lambda user: create_preview('confirm_create_testcase', arguments, user))


def preview_import_recorded_script(ctx: Context, project_id: int, content: str,
                                   name: str = '', start_url: str = '',
                                   browser: str = 'chromium', device: str = '',
                                   save_storage: bool = False) -> dict:
    """Validate a Playwright recording and preview importing it to a UI project."""
    from apps.ui_automation.recording_import import analyze_recorded_script
    from apps.ui_automation.script_security import ScriptSecurityError

    arguments = {
        'project_id': project_id,
        'content': content,
        'name': name,
        'start_url': start_url,
        'browser': browser,
        'device': device,
        'save_storage': save_storage,
    }

    def run(user):
        if not act.accessible_ui_projects(user).filter(id=project_id).exists():
            raise ToolError(f'UI 项目 {project_id} 不存在或无权限')
        try:
            analysis = analyze_recorded_script(content)
        except ScriptSecurityError as exc:
            raise act.McpActionError(str(exc)) from exc
        arguments['content_hash'] = analysis['content_hash']
        return create_preview('confirm_import_recorded_script', arguments, user)

    return _run_tool(ctx, 'preview_import_recorded_script', arguments, run)


def confirm_import_recorded_script(ctx: Context, confirm_token: str) -> dict:
    """Confirm importing a reviewed Playwright recording into its UI project."""
    return _run_tool(ctx, 'confirm_import_recorded_script', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def confirm_create_testcase(ctx: Context, confirm_token: str) -> dict:
    """确认创建功能测试用例（危险操作第二步）。

    校验 preview_create_testcase 返回的 confirm_token 后创建用例，返回 testcase_id。
    令牌一次性消费且 5 分钟内有效；人工审批模式下返回 awaiting_approval，
    需轮询 get_approval_status。
    """
    return _run_tool(ctx, 'confirm_create_testcase', {'confirm_token': '<token>'},
                     lambda user: consume_pending(verify_pending(confirm_token, user), user))


def get_approval_status(ctx: Context, confirm_token: str) -> dict:
    """查询危险操作的人工审批状态（只读，供 Agent 轮询）

    人工审批模式下 confirm_* 返回 awaiting_approval 后，用同一
    confirm_token 反复调用本工具，直至返回 approved（附执行结果）/
    rejected / expired。
    """
    return _run_tool(ctx, 'get_approval_status', {'confirm_token': '<token>'},
                     lambda user: query_approval_status(confirm_token, user))


def get_ui_execution(ctx: Context, execution_id: int) -> dict:
    """查询 UI 自动化用例执行结果（confirm_run_ui_case 返回执行 ID 后轮询本工具）。

    返回执行状态（pending/running/passed/failed/error）、错误信息、逐步骤结果、
    耗时与截图元数据（不含 base64 图片数据，截图请通过平台页面查看）。
    状态为 running/pending 时请间隔数秒后再次调用。需对执行所属 UI 项目有访问权限。
    """

    def run(user):
        from apps.ui_automation.models import TestCaseExecution
        execution = TestCaseExecution.objects.filter(
            id=execution_id, project__in=act.accessible_ui_projects(user)
        ).select_related('test_case').first()
        if not execution:
            raise ToolError(f'UI 执行 {execution_id} 不存在或无权限')

        # execution_logs 落库为步骤结果 JSON（与 views.run 保存格式一致）；
        # 历史文本日志或异常数据降级为 raw_logs 返回
        steps = []
        raw_logs = ''
        logs = execution.execution_logs or ''
        if logs:
            try:
                parsed = json.loads(logs)
                if isinstance(parsed, list):
                    steps = parsed
                else:
                    raw_logs = logs
            except (TypeError, ValueError):
                raw_logs = logs

        result = {
            'execution_id': execution.id,
            'case_id': execution.test_case_id,
            'case_name': execution.test_case.name if execution.test_case else '',
            'status': execution.status,
            'engine': execution.engine,
            'browser': execution.browser,
            'headless': execution.headless,
            'error_message': execution.error_message or '',
            'steps': steps,
            'execution_time': execution.execution_time,
            'started_at': execution.started_at,
            'finished_at': execution.finished_at,
            # 截图只返回元数据，base64 原图体积过大不适合 MCP 通道传输
            'screenshots': [
                {k: v for k, v in shot.items() if k != 'url'}
                for shot in (execution.screenshots or []) if isinstance(shot, dict)
            ],
        }
        if raw_logs:
            result['raw_logs'] = raw_logs[:2000]
        if execution.status in ('running', 'pending'):
            result['hint'] = '执行尚未结束，请数秒后再次调用本工具'
        return result

    return _run_tool(ctx, 'get_ui_execution', {'execution_id': execution_id}, run)


#: 所有工具（供 server.py 批量注册）
ALL_TOOLS = [
    # 只读：项目 / 用例 / 场景 / 套件 / 元素库 / 执行结果
    list_projects, list_testcases, list_testcase_modules, get_testcase,
    list_api_requests, get_api_request, list_api_collections, list_api_scenarios,
    list_scenario_case_folders, list_api_suites,
    list_ui_cases, list_ui_case_folders, list_ui_suites, list_ui_elements,
    list_perf_scenes, get_report, analyze_failure,
    get_ui_execution, get_ui_suite_execution,
    get_api_scenario_execution, get_api_suite_execution,
    # 执行类（preview → confirm）
    preview_run_api_suite, confirm_run_api_suite,
    preview_run_api_scenario, confirm_run_api_scenario,
    preview_run_ui_case, confirm_run_ui_case,
    preview_run_ui_suite, confirm_run_ui_suite,
    preview_run_perf_scene, confirm_run_perf_scene,
    # 创建 / 更新类（preview → confirm）
    preview_create_testcase, confirm_create_testcase,
    preview_update_testcase, confirm_update_testcase,
    preview_create_ui_case, confirm_create_ui_case,
    preview_update_ui_case, confirm_update_ui_case,
    preview_create_ui_element, confirm_create_ui_element,
    preview_create_api_scenario_case, confirm_create_api_scenario_case,
    preview_create_api_request, confirm_create_api_request,
    preview_update_api_scenario_case, confirm_update_api_scenario_case,
    preview_update_ui_element, confirm_update_ui_element,
    preview_import_recorded_script, confirm_import_recorded_script,
    # 审批状态查询
    get_approval_status,
]
