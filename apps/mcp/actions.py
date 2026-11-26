"""MCP 工具的实际执行动作与项目权限过滤。

危险工具（run_*/create_*）的执行体统一在此实现，preview/confirm 工具
与监控页「手动批准」共用同一入口，保证行为一致。
"""
import hashlib
import json
import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# 权限过滤：四类项目均按 owner/members 过滤，与各模块视图 get_queryset 一致
# --------------------------------------------------------------------- #

def accessible_projects(user):
    from apps.projects.models import Project
    return Project.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def accessible_api_projects(user):
    from apps.api_testing.models import ApiProject
    return ApiProject.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def accessible_ui_projects(user):
    from apps.ui_automation.models import UiProject
    return UiProject.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def accessible_perf_projects(user):
    from apps.perf_testing.models import PerfProject
    return PerfProject.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def args_digest(arguments: dict) -> str:
    """参数摘要（仅存哈希，不落原始参数）。"""
    try:
        raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        raw = repr(arguments)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


#: 参数脱敏：键名含以下子串的字段值替换为 ***
_SENSITIVE_KEY_PARTS = ('password', 'token', 'key', 'secret', 'credential')
#: args_brief 最大长度（截断控制表体积）
ARGS_BRIEF_MAX_LEN = 2000


def _mask_value_tree(value):
    """递归脱敏：dict 按键名判断，list 逐项递归。"""
    if isinstance(value, dict):
        masked = {}
        for k, v in value.items():
            if any(part in str(k).lower() for part in _SENSITIVE_KEY_PARTS):
                masked[k] = '***'
            else:
                masked[k] = _mask_value_tree(v)
        return masked
    if isinstance(value, list):
        return [_mask_value_tree(item) for item in value]
    return value


def mask_sensitive_args(arguments: dict, max_len: int = ARGS_BRIEF_MAX_LEN) -> str:
    """参数脱敏截断文本：敏感字段值替换为 ***，超长截断，供审计回溯。"""
    try:
        raw = json.dumps(_mask_value_tree(arguments or {}),
                         ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - 序列化失败降级为 repr
        raw = repr(arguments)
    if len(raw) > max_len:
        raw = raw[:max_len] + '...(已截断)'
    return raw


# --------------------------------------------------------------------- #
# 危险操作执行体：每个函数返回 dict 结果；失败抛 McpActionError
# --------------------------------------------------------------------- #

class McpActionError(Exception):
    """动作执行失败（面向 Agent 的可读错误）。"""


# --------------------------------------------------------------------- #
# 公共辅助：字段归一化 / 校验
# --------------------------------------------------------------------- #

def _resolve_priority(value, choices, default, field_label='data.priority'):
    """按模型自身枚举校验优先级（大小写不敏感，支持该枚举的中文别名）。

    功能用例为 low/medium/high/critical（低/中/高/紧急），
    UI 自动化用例为 high/medium/low（高/中/低）；
    中文别名直接取自模型 choices 的显示名，因此只会映射到该模型真实存在的取值。
    不再兼容 P0-P3 等外部写法，非法值直接报错而不是静默回退。
    """
    allowed = dict(choices)
    if value is None or str(value).strip() == '':
        return default
    raw = str(value).strip()
    lowered = raw.lower()
    if lowered in allowed:
        return lowered
    for canonical, label in allowed.items():
        if raw == label:
            return canonical
    raise McpActionError(
        f'{field_label} 无效: {value}，可选: {", ".join(allowed)}'
        f'（或对应中文: {"/".join(allowed.values())}）')


def _coerce_steps(value):
    """把步骤入参归一化为 [{id?, sort_order?, action, expected}]。
    字符串按 `#` 拆分；数组元素可带 id / sort_order，用于按主键增量更新。
    """
    from apps.testcases.steps import parse_step_pairs

    if value is None:
        return []
    if isinstance(value, str):
        return parse_step_pairs(value, '')
    if not isinstance(value, (list, tuple)):
        raise McpActionError('data.steps 必须是数组或文本')
    steps = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise McpActionError(f'步骤 {index} 必须是对象')
        step = {
            'action': str(item.get('action') or item.get('step') or '').strip(),
            'expected': str(item.get('expected') or '').strip(),
        }
        if item.get('id'):
            step['id'] = item['id']
        if item.get('sort_order') is not None:
            step['sort_order'] = item['sort_order']
        steps.append(step)
    return steps


def _resolve_locator_strategy(value):
    """按 ID 或名称（大小写不敏感）解析元素定位策略。"""
    from apps.ui_automation.models import LocatorStrategy

    if value is None or value == '':
        raise McpActionError('data.locator_strategy 不能为空（定位策略 ID 或名称，如 CSS/XPath/text）')
    raw = str(value).strip()
    strategy = None
    if raw.isdigit():
        strategy = LocatorStrategy.objects.filter(pk=int(raw)).first()
    if strategy is None:
        strategy = LocatorStrategy.objects.filter(name__iexact=raw).first()
    if strategy is None:
        available = ', '.join(LocatorStrategy.objects.values_list('name', flat=True)[:20])
        raise McpActionError(f'定位策略「{raw}」不存在，可用策略: {available}')
    return strategy


def _normalize_backup_locators(value):
    """校验并归一化备用定位器列表：[{strategy, value}]。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise McpActionError('data.backup_locators 必须是数组')
    normalized = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict) or not item.get('strategy') or not item.get('value'):
            raise McpActionError(f'备用定位器 {index} 需要 strategy 与 value')
        normalized.append({'strategy': str(item['strategy']), 'value': str(item['value'])})
    return normalized


def _build_ui_steps(project_id, steps):
    """校验并归一化 UI 自动化用例步骤（元素必须属于同一项目）。"""
    from apps.ui_automation.models import Element, TestCaseStep
    from apps.ui_automation.serializers import validate_test_case_step_data
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import ValidationError as DRFValidationError

    if not isinstance(steps, (list, tuple)):
        raise McpActionError('data.steps 必须是数组')
    valid_actions = {value for value, _ in TestCaseStep.ACTION_TYPE_CHOICES}
    valid_assert_types = {value for value, _ in TestCaseStep.ASSERT_TYPE_CHOICES}

    cleaned = []
    for index, raw_step in enumerate(steps, 1):
        if not isinstance(raw_step, dict):
            raise McpActionError(f'步骤 {index} 必须是对象')
        step = dict(raw_step)
        action_type = step.get('action_type', 'click')
        assert_type = step.get('assert_type', '') or ''
        if action_type not in valid_actions:
            raise McpActionError(f'步骤 {index} 的操作类型无效: {action_type}')
        if assert_type and assert_type not in valid_assert_types:
            raise McpActionError(f'步骤 {index} 的断言类型无效: {assert_type}')

        element = None
        element_id = step.get('element_id') or step.get('element')
        if element_id:
            element = Element.objects.filter(id=element_id, project_id=project_id).first()
            if not element:
                raise McpActionError(f'步骤 {index} 的目标元素 {element_id} 不属于当前项目')

        action_params = step.get('action_params') or {}
        if not isinstance(action_params, dict):
            raise McpActionError(f'步骤 {index} 的 action_params 必须是对象')

        try:
            validate_test_case_step_data(
                action_type=action_type,
                assert_type=assert_type,
                input_value=step.get('input_value', ''),
                assert_value=step.get('assert_value', ''),
                element=element,
                project_id=project_id,
                action_params=action_params,
            )
        except (DRFValidationError, DjangoValidationError) as exc:
            detail = getattr(exc, 'detail', None)
            if isinstance(detail, (list, tuple)) and detail:
                detail = detail[0]
            raise McpActionError(f'步骤 {index}: {detail or exc}') from exc

        cleaned.append({
            'step_number': index,
            'action_type': action_type,
            'element_id': element.id if element else None,
            'input_value': step.get('input_value', '') or '',
            'action_params': action_params,
            'wait_time': int(step.get('wait_time', 1000) or 1000),
            'assert_type': assert_type,
            'assert_value': step.get('assert_value', '') or '',
            'description': step.get('description', '') or '',
        })
    return cleaned


def _replace_ui_steps(case, steps):
    """用给定步骤整体替换用例步骤（与页面保存语义一致）。

    删除与重建放在同一事务内，避免中途失败导致原步骤丢失。
    """
    from django.db import transaction
    from apps.ui_automation.models import TestCaseStep

    with transaction.atomic():
        case.steps.all().delete()
        TestCaseStep.objects.bulk_create([
            TestCaseStep(test_case=case, **step) for step in steps
        ])
    return len(steps)


def _element_validation_warning(element):
    """元素定位器未验证时的提示文案（VALID 返回空串）。"""
    if element.validation_status == 'VALID':
        return ''
    return (f'定位器未经验证（validation_status={element.validation_status}）：'
            '平台暂无真实 DOM 校验能力，请确认 selector 来自录制脚本、元素库或人工确认，'
            '否则用例可能执行失败')


def _unverified_element_warnings(project_id, steps):
    """收集步骤中「定位器未验证」的元素，提示用例可能无法执行。

    平台没有真实的 DOM 定位器校验能力（ElementViewSet.validate_locator 是模拟实现），
    因此只能把元素的 validation_status 如实透出，由调用方决定是否先验证。
    """
    from apps.ui_automation.models import Element

    element_ids = {step.get('element_id') for step in (steps or [])
                   if isinstance(step, dict) and step.get('element_id')}
    if not element_ids:
        return []
    unverified = Element.objects.filter(
        project_id=project_id, id__in=element_ids).exclude(validation_status='VALID')
    return [f'元素 {element.id}「{element.name}」定位器未验证'
            f'（validation_status={element.validation_status}），可能导致执行失败'
            for element in unverified]


def _resolve_formal_test_case(test_case_id, user, for_update=False):
    """按 ID 取功能测试用例并校验访问权限。

    for_update=True 时加行锁（必须在事务内调用），用于串行化并发关联操作。
    """
    from apps.testcases.models import TestCase

    queryset = TestCase.objects.filter(
        id=test_case_id, project__in=accessible_projects(user)
    ).select_related('project')
    if for_update:
        queryset = queryset.select_for_update()
    case = queryset.first()
    if not case:
        raise McpActionError(f'功能测试用例 {test_case_id} 不存在或无权限')
    return case


def _resolve_automation_target(case_type: str, user, workspace_project_id, object_id):
    """解析可关联的自动化用例：必须位于同一统一项目下的自动化项目中。"""
    if case_type == 'ui_test_case':
        from apps.ui_automation.models import TestCase as UiCase, UiProject
        projects = UiProject.objects.filter(
            Q(owner=user) | Q(members=user), workspace_project_id=workspace_project_id
        ).distinct()
        return UiCase.objects.filter(project__in=projects, pk=object_id).select_related('project').first()
    if case_type == 'api_scenario_case':
        from apps.api_testing.models import ApiProject, ScenarioCase
        projects = ApiProject.objects.filter(
            Q(owner=user) | Q(members=user), workspace_project_id=workspace_project_id
        ).distinct()
        return ScenarioCase.objects.filter(project__in=projects, pk=object_id).select_related('project').first()
    raise McpActionError(f'不支持的自动化用例类型: {case_type}')


def structural_target_names(case_type: str, workspace_project_id, object_ids):
    """按「结构性存在」解析关联目标名称（不校验当前用户权限）。

    目标仍存在且其自动化项目仍绑定同一统一项目才算有效；目标被删除、
    被移到其他项目或项目解绑时返回空，供补齐流程识别悬空关联。
    """
    object_ids = set(object_ids or ())
    if not object_ids:
        return {}
    if case_type == 'ui_test_case':
        from apps.ui_automation.models import TestCase as UiCase
        queryset = UiCase.objects.filter(
            id__in=object_ids, project__workspace_project_id=workspace_project_id)
    elif case_type == 'api_scenario_case':
        from apps.api_testing.models import ScenarioCase
        queryset = ScenarioCase.objects.filter(
            id__in=object_ids, project__workspace_project_id=workspace_project_id)
    else:
        return {}
    return dict(queryset.values_list('id', 'name'))


def _link_formal_case(test_case, case_type: str, object_id: int, user):
    """把自动化用例关联到功能测试用例（同类型仅保留一条，已存在则替换）。

    调用方必须先经 `_ensure_link_replaceable` 确认允许替换，避免静默覆盖。
    """
    from apps.testcases.models import TestCaseAutomationLink

    target = _resolve_automation_target(case_type, user, test_case.project_id, object_id)
    if not target:
        raise McpActionError(
            f'目标自动化用例 {object_id} 不存在、与功能用例不在同一项目或无权访问')
    link, created = TestCaseAutomationLink.objects.update_or_create(
        test_case=test_case,
        case_type=case_type,
        defaults={'object_id': target.id, 'linked_by': user},
    )
    return {
        'link_id': link.id,
        'case_type': case_type,
        'object_id': target.id,
        'target_name': target.name,
        'target_project_id': target.project_id,
        'replaced': not created,
    }


_CASE_TYPE_LABELS = {
    'ui_test_case': 'UI 自动化用例',
    'api_scenario_case': '接口场景用例',
}


def _existing_link(test_case, case_type):
    """取该功能用例指定类型的现有关联（同类型唯一）。"""
    from apps.testcases.models import TestCaseAutomationLink

    return TestCaseAutomationLink.objects.filter(
        test_case=test_case, case_type=case_type).first()


def _link_conflict_message(test_case, case_type, existing):
    label = _CASE_TYPE_LABELS.get(case_type, case_type)
    target_name = ''
    try:
        target_name = structural_target_names(
            case_type, test_case.project_id, {existing.object_id}
        ).get(existing.object_id, '')
    except Exception:  # noqa: BLE001 - 名称解析失败不影响报错
        pass
    suffix = f'「{target_name}」' if target_name else ''
    return (f'功能用例 {test_case.id}「{test_case.title}」已关联 {label}'
            f'{suffix}（object_id={existing.object_id}）；'
            '为避免覆盖已有关联，本次创建被拒绝。'
            '确需替换请显式传 replace_link=true，或先删除原关联。')


def _ensure_link_replaceable(test_case, case_type, replace_link):
    """已有关联且未显式要求替换时报错，避免新建对象后静默顶掉原链接。"""
    existing = _existing_link(test_case, case_type)
    if existing is None or replace_link:
        return existing
    raise McpActionError(_link_conflict_message(test_case, case_type, existing))


def _link_preview_note(test_case_id, case_type, replace_link):
    """预览里说明关联动作：新建 / 替换 / 已存在会被拒绝。"""
    if not test_case_id:
        return ''
    from apps.testcases.models import TestCaseAutomationLink

    existing = TestCaseAutomationLink.objects.filter(
        test_case_id=test_case_id, case_type=case_type).first()
    if existing is None:
        return f'，并关联功能用例 {test_case_id}'
    label = _CASE_TYPE_LABELS.get(case_type, case_type)
    if replace_link:
        return (f'，并替换功能用例 {test_case_id} 已有的 {label}'
                f'（object_id={existing.object_id}）')
    return (f'；注意：功能用例 {test_case_id} 已关联 {label}'
            f'（object_id={existing.object_id}），未传 replace_link=true 时确认会被拒绝')


def _resolve_folder(folder_id, project_id, model):
    """解析目录并校验归属项目。"""
    if not folder_id:
        return None
    folder = model.objects.filter(pk=folder_id).first()
    if not folder:
        raise McpActionError(f'目录 {folder_id} 不存在')
    if folder.project_id != int(project_id):
        raise McpActionError(f'目录 {folder_id} 不属于当前项目')
    return folder


def _normalize_folder_path(value):
    """把 'A / B/C ' 规范化为 ['A', 'B', 'C']（忽略空段与首尾空白）。"""
    return [segment.strip() for segment in str(value or '').split('/') if segment.strip()]


def _folder_path_candidates(model, project_id, name, parent_field='parent', limit=5):
    """列出项目内同名目录的完整路径，用于同名歧义提示。"""
    parent_attname = f'{parent_field}_id'
    rows = list(model.objects.filter(project_id=project_id).values(
        'id', 'name', parent_attname))
    by_id = {row['id']: row for row in rows}
    folded = str(name or '').casefold()
    hits = []
    for row in rows:
        if row['name'].casefold() != folded:
            continue
        names, cursor, guard = [row['name']], row[parent_attname], {row['id']}
        while cursor and cursor in by_id and cursor not in guard:
            guard.add(cursor)
            names.append(by_id[cursor]['name'])
            cursor = by_id[cursor][parent_attname]
        hits.append('/'.join(reversed(names)))
    return sorted(hits)[:limit]


def _resolve_folder_path(model, project_id, path, field='data.folder_path',
                         label='目录', parent_field='parent',
                         create_missing=False, name_max_len=200):
    """按 'A/B/C' 路径解析目录，返回目录对象（不存在时按需创建）。

    - 逐级匹配：先精确、再忽略大小写，路径本身消除跨父级同名歧义；
    - 单段名称（只知名称、不知父级）在根级找不到时，退化为全项目同名匹配：
      唯一命中直接使用，多个命中报歧义并列出完整路径；
    - 中间层缺失直接报错（不允许凭空补父级，避免建出错误层级）；
    - 末级缺失：create_missing=False 报错并列出同级候选，True 时创建。
    """
    segments = _normalize_folder_path(path)
    if not segments:
        raise McpActionError(f'{field} 不能为空，格式为「父目录/子目录」')
    full_path = '/'.join(segments)
    parent = None
    for depth, segment in enumerate(segments, 1):
        is_last = depth == len(segments)
        siblings = model.objects.filter(
            project_id=project_id, **{parent_field: parent})
        matches = list(siblings.filter(name=segment))
        if not matches:
            matches = list(siblings.filter(name__iexact=segment))
        if len(matches) > 1:
            candidates = _folder_path_candidates(
                model, project_id, segment, parent_field=parent_field)
            raise McpActionError(
                f'{label}「{segment}」在同级存在 {len(matches)} 个同名项，'
                f'请用完整路径指定：{", ".join(candidates) or full_path}')
        if matches:
            parent = matches[0]
            continue

        # 只给一个名称且根级没有：按全项目同名匹配，解决「只知名称不知父级」
        if is_last and len(segments) == 1:
            anywhere = list(model.objects.filter(
                project_id=project_id, name=segment))
            if not anywhere:
                anywhere = list(model.objects.filter(
                    project_id=project_id, name__iexact=segment))
            if len(anywhere) == 1:
                return anywhere[0]
            if len(anywhere) > 1:
                candidates = _folder_path_candidates(
                    model, project_id, segment, parent_field=parent_field)
                raise McpActionError(
                    f'{label}「{segment}」在项目内存在 {len(anywhere)} 个同名项，'
                    f'请用完整路径指定：{", ".join(candidates)}')

        if not (is_last and create_missing):
            available = ', '.join(
                siblings.values_list('name', flat=True)[:10]) or '（无子目录）'
            prefix = '/'.join(segments[:depth - 1])
            where = f'「{prefix}」下' if prefix else ''
            hint = ('；如需自动创建末级目录请传 create_missing=true' if is_last
                    else '；请先创建上级目录')
            raise McpActionError(
                f'{label}路径 {full_path} 不存在：{where}没有「{segment}」，'
                f'现有子目录: {available}{hint}')
        parent = model.objects.create(
            project_id=project_id, name=segment[:name_max_len],
            **{parent_field: parent})
    return parent


def _resolve_folder_target(folder_id, folder_path, project_id, model,
                           field='data.folder_path', label='目录',
                           parent_field='parent', create_missing=False,
                           name_max_len=200):
    """目录定位：ID 优先，其次按路径；都没传返回 None。"""
    if folder_id:
        return _resolve_folder(folder_id, project_id, model)
    if not str(folder_path or '').strip():
        return None
    return _resolve_folder_path(
        model, project_id, folder_path, field=field, label=label,
        parent_field=parent_field, create_missing=create_missing,
        name_max_len=name_max_len)


def _resolve_environment(environment_id, project_id):
    """解析接口测试环境并校验归属项目。"""
    from apps.api_testing.models import Environment

    if not environment_id:
        return None
    environment = Environment.objects.filter(pk=environment_id).first()
    if not environment:
        raise McpActionError(f'执行环境 {environment_id} 不存在')
    if environment.project_id and environment.project_id != int(project_id):
        raise McpActionError(f'执行环境 {environment_id} 不属于当前项目')
    return environment


def _resolve_collection(project, collection_id, collection_name):
    """解析接口集合：优先按 ID，其次按名称复用，最后按名称新建。

    返回 (collection, created)。集合是接口请求的归属目录，场景用例与
    list_api_requests 都按「集合所属项目」过滤，所以新建请求必须挂到集合下。
    """
    from apps.api_testing.models import ApiCollection

    if collection_id:
        collection = ApiCollection.objects.filter(
            id=collection_id, project=project).first()
        if not collection:
            raise McpActionError(f'集合 {collection_id} 不存在或不属于当前项目')
        return collection, False

    name = str(collection_name or '').strip()
    if not name:
        available = ', '.join(
            ApiCollection.objects.filter(project=project).values_list('name', flat=True)[:20]
        ) or '（该项目暂无集合，可传 collection_name 自动新建）'
        raise McpActionError(
            f'data.collection_id 或 collection_name 必填，现有集合: {available}')
    collection = (ApiCollection.objects.filter(project=project, name=name).first()
                  or ApiCollection.objects.filter(project=project, name__iexact=name).first())
    if collection:
        return collection, False
    return ApiCollection.objects.create(project=project, name=name[:200]), True


# --------------------------------------------------------------------- #
# 执行类动作
# --------------------------------------------------------------------- #


def run_api_suite_action(arguments: dict, user) -> dict:
    from apps.api_testing.models import TestSuite
    from apps.api_testing.mcp_runner import start_suite_execution

    suite_id = arguments.get('suite_id')
    suite = TestSuite.objects.filter(
        id=suite_id, project__in=accessible_api_projects(user)
    ).select_related('project').first()
    if not suite:
        raise McpActionError(f'测试套件 {suite_id} 不存在或无权限')

    environment = _resolve_environment(arguments.get('environment_id'), suite.project_id)
    try:
        execution = start_suite_execution(suite, user, environment=environment)
    except ValueError as exc:
        raise McpActionError(str(exc)) from exc
    return {
        'execution_id': execution.id,
        'suite_id': suite.id,
        'suite_name': suite.name,
        'total_scenarios': execution.total_scenarios,
        'status': execution.status,
        'hint': '套件已异步启动，请调用 get_api_suite_execution 轮询执行结果',
    }


def run_api_scenario_action(arguments: dict, user) -> dict:
    from apps.api_testing.models import ScenarioCase
    from apps.api_testing.mcp_runner import start_scenario_execution

    scenario_case_id = arguments.get('scenario_case_id')
    scenario_case = ScenarioCase.objects.filter(
        id=scenario_case_id, project__in=accessible_api_projects(user)
    ).select_related('project').first()
    if not scenario_case:
        raise McpActionError(f'场景用例 {scenario_case_id} 不存在或无权限')

    environment = _resolve_environment(
        arguments.get('environment_id'), scenario_case.project_id)
    execution = start_scenario_execution(scenario_case, user, environment=environment)
    return {
        'execution_id': execution.id,
        'scenario_case_id': scenario_case.id,
        'scenario_case_name': scenario_case.name,
        'total_requests': execution.total_requests,
        'status': execution.status,
        'hint': '场景用例已异步启动，请调用 get_api_scenario_execution 轮询执行结果',
    }


def run_ui_suite_action(arguments: dict, user) -> dict:
    from apps.ui_automation.models import TestSuite
    from apps.ui_automation.mcp_runner import start_suite_execution

    suite_id = arguments.get('suite_id')
    suite = TestSuite.objects.filter(
        id=suite_id, project__in=accessible_ui_projects(user)
    ).select_related('project').first()
    if not suite:
        raise McpActionError(f'UI 套件 {suite_id} 不存在或无权限')

    engine = arguments.get('engine', 'playwright')
    if engine not in ('playwright', 'selenium'):
        raise McpActionError(f'不支持的引擎: {engine}')
    browser = arguments.get('browser', 'chrome')
    headless = bool(arguments.get('headless', True))
    storage_state_id = arguments.get('storage_state_id') or None

    try:
        execution = start_suite_execution(
            suite, user, engine=engine, browser=browser,
            headless=headless, storage_state_id=storage_state_id)
    except ValueError as exc:
        raise McpActionError(str(exc)) from exc
    return {
        'execution_id': execution.id,
        'suite_id': suite.id,
        'suite_name': suite.name,
        'engine': engine,
        'browser': browser,
        'status': execution.status,
        'hint': '套件已异步启动，请调用 get_ui_suite_execution 轮询执行结果',
    }


def run_ui_case_action(arguments: dict, user) -> dict:
    from apps.ui_automation.models import TestCase
    from apps.ui_automation.mcp_runner import start_case_execution

    case_id = arguments.get('case_id')
    case = TestCase.objects.filter(
        id=case_id, project__in=accessible_ui_projects(user)
    ).select_related('project').first()
    if not case:
        raise McpActionError(f'UI 用例 {case_id} 不存在或无权限')

    engine = arguments.get('engine', 'playwright')
    if engine not in ('playwright', 'selenium'):
        raise McpActionError(f'不支持的引擎: {engine}')
    browser = arguments.get('browser', 'chrome')
    headless = bool(arguments.get('headless', True))

    # 与页面 views.TestCaseViewSet.run 一致：登录态步骤只有 Playwright 引擎支持。
    if engine == 'selenium' and case.steps.filter(action_type='storageState').exists():
        raise McpActionError('该用例包含登录态（storageState）步骤，必须使用 playwright 引擎执行')

    try:
        execution = start_case_execution(case, user, engine=engine,
                                         browser=browser, headless=headless)
    except ValueError as e:
        raise McpActionError(str(e)) from e
    return {
        'execution_id': execution.id,
        'case_id': case.id,
        'case_name': case.name,
        'engine': engine,
        'status': execution.status,
        'hint': '执行已异步启动，可通过执行 ID 查询结果',
    }


def run_perf_scene_action(arguments: dict, user) -> dict:
    from apps.perf_testing.models import PerfScenario
    from apps.perf_testing.services import executor

    scene_id = arguments.get('scene_id')
    scenario = PerfScenario.objects.filter(
        id=scene_id, project__in=accessible_perf_projects(user)
    ).select_related('project').first()
    if not scenario:
        raise McpActionError(f'压测场景 {scene_id} 不存在或无权限')

    if scenario.has_active_execution():
        raise McpActionError('该场景已有正在执行的压测，请等待结束或先停止')

    execution, check = executor.start_execution(
        scenario, user=user, trigger_type='API')
    if execution is None:
        raise McpActionError(f'执行前检查未通过: {check}')
    return {
        'execution_id': execution.id,
        'execution_no': execution.execution_no,
        'scenario_name': scenario.name,
        'status': execution.status,
        'hint': '压测已异步启动，可通过执行 ID 查询结果',
    }


def _apply_testcase_fields(testcase, data: dict, project_id: int) -> dict:
    """把功能用例字段写入实例（创建/更新共用），返回变更字段名列表。

    步骤通过 `steps` 传入 [{action, expected}] 数组，由调用方整体替换。
    """
    from apps.testcases.models import TestCase, TestCaseModule

    changed = []
    text_fields = ('title', 'description', 'notes', 'preconditions')
    for field in text_fields:
        if field not in data:
            continue
        value = data.get(field)
        if field == 'title':
            value = (value or '').strip()
            if not value:
                raise McpActionError('data.title 不能为空')
            value = value[:500]
        setattr(testcase, field, '' if value is None else str(value))
        changed.append(field)

    if 'priority' in data:
        testcase.priority = _resolve_priority(
            data.get('priority'), TestCase.PRIORITY_CHOICES, testcase.priority)
        changed.append('priority')

    if 'test_type' in data:
        test_type = data.get('test_type')
        if test_type not in dict(TestCase.TYPE_CHOICES):
            raise McpActionError(
                f"data.test_type 无效: {test_type}，可选: {', '.join(dict(TestCase.TYPE_CHOICES))}")
        testcase.test_type = test_type
        changed.append('test_type')

    if 'status' in data:
        status = data.get('status')
        if status not in dict(TestCase.STATUS_CHOICES):
            raise McpActionError(
                f"data.status 无效: {status}，可选: {', '.join(dict(TestCase.STATUS_CHOICES))}")
        testcase.status = status
        changed.append('status')

    if 'tags' in data:
        tags = data.get('tags') or []
        if not isinstance(tags, (list, tuple)):
            raise McpActionError('data.tags 必须是字符串数组')
        testcase.tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        changed.append('tags')

    if 'module_id' in data or 'module_path' in data:
        module_id = data.get('module_id')
        module_path = str(data.get('module_path') or '').strip()
        if module_id:
            module = TestCaseModule.objects.filter(id=module_id, project_id=project_id).first()
            if not module:
                raise McpActionError(f'用例模块 {module_id} 不存在或不属于当前项目')
            testcase.module = module
        elif module_path:
            testcase.module = _resolve_folder_path(
                TestCaseModule, project_id, module_path,
                field='data.module_path', label='用例模块',
                create_missing=bool(data.get('create_missing')), name_max_len=100)
        else:
            testcase.module = None
        changed.append('module')

    if 'assignee_id' in data:
        assignee_id = data.get('assignee_id')
        if assignee_id:
            from apps.users.models import User as UserModel
            assignee = UserModel.objects.filter(id=assignee_id, is_active=True).first()
            if not assignee:
                raise McpActionError(f'指派人 {assignee_id} 不存在')
            testcase.assignee = assignee
        else:
            testcase.assignee = None
        changed.append('assignee')

    return changed


def _sync_testcase_step_details(testcase, steps, ignore_unknown_ids=False):
    """按主键增量同步功能用例步骤（与预期结果一一对应）。

    带 id 的原地更新、不带 id 的新建、未提交的删除；id 必须属于当前用例。
    ``ignore_unknown_ids=True`` 用于新建用例：此时还没有任何步骤记录，
    id 没有意义，直接忽略（并把被忽略的 id 回显给调用方），而不是报错。
    """
    from apps.testcases.steps import sync_steps, validate_step_payload

    if not isinstance(steps, (list, tuple)):
        raise McpActionError('data.steps 必须是数组')
    prepared = _coerce_steps(steps)
    valid_ids = set(testcase.step_details.values_list('id', flat=True))
    ignored_ids = []
    for item in prepared:
        step_id = item.get('id')
        if not step_id or step_id in valid_ids:
            continue
        if not ignore_unknown_ids:
            raise McpActionError(f'步骤 {step_id} 不属于当前用例')
        item.pop('id')
        if step_id not in ignored_ids:
            ignored_ids.append(step_id)
    # 与 REST / Excel 导入共用同一套校验：操作描述必填、长度上限、id 唯一且归属当前用例。
    errors = validate_step_payload(prepared, allowed_ids=valid_ids)
    if errors:
        raise McpActionError('；'.join(errors))
    result = sync_steps(testcase, prepared)
    result['ignored_ids'] = ignored_ids
    return result


def create_testcase_action(arguments: dict, user) -> dict:
    from django.db import transaction
    from apps.testcases.models import TestCase

    project_id = arguments.get('project_id')
    project = accessible_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'项目 {project_id} 不存在或无权限')

    data = arguments.get('data') or {}
    title = (data.get('title') or '').strip()
    if not title:
        raise McpActionError('data.title 不能为空')

    testcase = TestCase(project=project, author=user)
    # 用例主体与结构化步骤同事务落库，避免步骤校验失败留下半条数据。
    with transaction.atomic():
        _apply_testcase_fields(testcase, {**data, 'title': title}, project.id)
        testcase.save()

        version_ids = data.get('version_ids') or []
        if version_ids:
            testcase.versions.set(version_ids)

        steps_result = {'created': 0, 'updated': 0, 'deleted': 0, 'ignored_ids': []}
        if 'steps' in data:
            # 新建用例还没有步骤记录，传上来的 id 无意义，忽略并回显。
            steps_result = _sync_testcase_step_details(
                testcase, data.get('steps') or [], ignore_unknown_ids=True)

    return {
        'testcase_id': testcase.id,
        'title': testcase.title,
        'project_id': project.id,
        'priority': testcase.priority,
        'test_type': testcase.test_type,
        'status': testcase.status,
        'module_id': testcase.module_id,
        'steps_created': steps_result['created'],
        'steps_ignored_ids': steps_result.get('ignored_ids') or [],
    }


def create_ui_case_action(arguments: dict, user) -> dict:
    """新建 UI 自动化用例，可同时关联到功能测试用例。

    用例、步骤与关联关系在同一事务内落库：任一步校验失败都不会留下孤立用例。
    """
    from django.db import transaction
    from apps.ui_automation.models import TestCase as UiCase, TestCaseFolder

    project_id = arguments.get('project_id')
    project = accessible_ui_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'UI 项目 {project_id} 不存在或无权限')

    data = arguments.get('data') or {}
    name = (data.get('name') or '').strip()
    if not name:
        raise McpActionError('data.name 不能为空')

    priority = _resolve_priority(data.get('priority'), UiCase.PRIORITY_CHOICES, 'medium')
    status = data.get('status', 'draft')
    if status not in dict(UiCase.STATUS_CHOICES):
        raise McpActionError(
            f"data.status 无效: {status}，可选: {', '.join(dict(UiCase.STATUS_CHOICES))}")

    folder = _resolve_folder_target(
        data.get('folder_id'), data.get('folder_path'), project.id, TestCaseFolder,
        field='data.folder_path', label='用例目录',
        create_missing=bool(data.get('create_missing')))
    steps = _build_ui_steps(project.id, data.get('steps') or [])
    test_case_id = arguments.get('test_case_id')
    replace_link = bool(arguments.get('replace_link'))

    with transaction.atomic():
        # 先锁住功能用例行再判重：并发补齐同一用例时串行化，避免各自建出
        # 自动化用例后互相顶掉关联，留下未关联的孤儿对象。
        formal_case = None
        if test_case_id:
            formal_case = _resolve_formal_test_case(test_case_id, user, for_update=True)
            _ensure_link_replaceable(formal_case, 'ui_test_case', replace_link)

        case = UiCase.objects.create(
            project=project,
            name=name[:200],
            description=data.get('description', '') or '',
            folder=folder,
            priority=priority,
            status=status,
            created_by=user,
        )
        steps_created = _replace_ui_steps(case, steps) if steps else 0

        result = {
            'case_id': case.id,
            'name': case.name,
            'project_id': project.id,
            'folder_id': case.folder_id,
            'priority': case.priority,
            'status': case.status,
            'steps_created': steps_created,
        }
        warnings = _unverified_element_warnings(project.id, steps)
        if warnings:
            result['warnings'] = warnings
        if formal_case is not None:
            result['automation_link'] = _link_formal_case(
                formal_case, 'ui_test_case', case.id, user)
    return result


def create_api_scenario_case_action(arguments: dict, user) -> dict:
    """新建接口测试场景用例，可同时关联到功能测试用例。

    场景用例、步骤树与关联关系在同一事务内落库：任一步校验失败都不会留下孤立用例。
    """
    from django.db import transaction
    from apps.api_testing.models import ScenarioCase, ScenarioCaseFolder

    project_id = arguments.get('project_id')
    project = accessible_api_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'API 项目 {project_id} 不存在或无权限')

    data = arguments.get('data') or {}
    name = (data.get('name') or '').strip()
    if not name:
        raise McpActionError('data.name 不能为空')

    folder = _resolve_folder_target(
        data.get('folder_id'), data.get('folder_path'), project.id, ScenarioCaseFolder,
        field='data.folder_path', label='场景目录',
        create_missing=bool(data.get('create_missing')))
    environment = _resolve_environment(data.get('environment_id'), project.id)
    test_case_id = arguments.get('test_case_id')
    replace_link = bool(arguments.get('replace_link'))

    steps = data.get('steps')
    if not isinstance(steps, list):
        raise McpActionError('data.steps 必须是步骤节点数组')

    with transaction.atomic():
        formal_case = None
        if test_case_id:
            formal_case = _resolve_formal_test_case(test_case_id, user, for_update=True)
            _ensure_link_replaceable(formal_case, 'api_scenario_case', replace_link)

        scenario_case = ScenarioCase.objects.create(
            project=project,
            name=name[:200],
            description=data.get('description', '') or '',
            folder=folder,
            environment=environment,
            created_by=user,
        )
        from apps.api_testing.scenario_service import save_definition
        save_definition(scenario_case, steps, user=user)

        result = {
            'scenario_case_id': scenario_case.id,
            'name': scenario_case.name,
            'project_id': project.id,
            'folder_id': scenario_case.folder_id,
            'environment_id': scenario_case.environment_id,
            'steps_created': len(steps),
        }
        if formal_case is not None:
            result['automation_link'] = _link_formal_case(
                formal_case, 'api_scenario_case', scenario_case.id, user)
    return result


def update_testcase_action(arguments: dict, user) -> dict:
    from django.db import transaction
    from apps.testcases.models import TestCase

    testcase_id = arguments.get('testcase_id')
    testcase = TestCase.objects.filter(
        id=testcase_id, project__in=accessible_projects(user)
    ).select_related('project').first()
    if not testcase:
        raise McpActionError(f'功能测试用例 {testcase_id} 不存在或无权限')

    data = arguments.get('data') or {}
    if not isinstance(data, dict) or not data:
        raise McpActionError('data 不能为空，请提供需要更新的字段')

    # 字段与结构化步骤同事务提交：步骤校验失败时不会留下部分更新。
    with transaction.atomic():
        changed = _apply_testcase_fields(testcase, data, testcase.project_id)
        testcase.save()

        if 'version_ids' in data:
            testcase.versions.set(data.get('version_ids') or [])
            changed.append('versions')

        steps_result = {'created': 0, 'updated': 0, 'deleted': 0}
        if 'steps' in data:
            steps_result = _sync_testcase_step_details(
                testcase, data.get('steps') or [])

    return {
        'testcase_id': testcase.id,
        'title': testcase.title,
        'project_id': testcase.project_id,
        'updated_fields': changed,
        'steps_replaced': steps_result['created'] + steps_result['updated'],
        'steps_updated': steps_result['updated'],
        'steps_deleted': steps_result['deleted'],
    }


def update_ui_case_action(arguments: dict, user) -> dict:
    from django.db import transaction
    from apps.ui_automation.models import TestCase as UiCase, TestCaseFolder

    case_id = arguments.get('case_id')
    case = UiCase.objects.filter(
        id=case_id, project__in=accessible_ui_projects(user)
    ).select_related('project').first()
    if not case:
        raise McpActionError(f'UI 用例 {case_id} 不存在或无权限')

    data = arguments.get('data') or {}
    if not isinstance(data, dict) or not data:
        raise McpActionError('data 不能为空，请提供需要更新的字段')

    # 步骤先校验后落库：校验失败时不改动主记录，也不丢失原步骤。
    steps = None
    if 'steps' in data:
        steps = _build_ui_steps(case.project_id, data.get('steps') or [])

    changed = []
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            raise McpActionError('data.name 不能为空')
        case.name = name[:200]
        changed.append('name')
    if 'description' in data:
        case.description = data.get('description') or ''
        changed.append('description')
    if 'priority' in data:
        case.priority = _resolve_priority(
            data.get('priority'), UiCase.PRIORITY_CHOICES, case.priority)
        changed.append('priority')
    if 'status' in data:
        status = data.get('status')
        if status not in dict(UiCase.STATUS_CHOICES):
            raise McpActionError(
                f"data.status 无效: {status}，可选: {', '.join(dict(UiCase.STATUS_CHOICES))}")
        case.status = status
        changed.append('status')
    if 'folder_id' in data or 'folder_path' in data:
        case.folder = _resolve_folder_target(
            data.get('folder_id'), data.get('folder_path'), case.project_id,
            TestCaseFolder, field='data.folder_path', label='用例目录',
            create_missing=bool(data.get('create_missing')))
        changed.append('folder')

    with transaction.atomic():
        case.save()
        steps_replaced = _replace_ui_steps(case, steps) if steps is not None else 0

    return {
        'case_id': case.id,
        'name': case.name,
        'project_id': case.project_id,
        'updated_fields': changed,
        'steps_replaced': steps_replaced,
    }


def update_api_scenario_case_action(arguments: dict, user) -> dict:
    from django.db import transaction
    from apps.api_testing.models import ScenarioCase, ScenarioCaseFolder

    scenario_case_id = arguments.get('scenario_case_id')
    scenario_case = ScenarioCase.objects.filter(
        id=scenario_case_id, project__in=accessible_api_projects(user)
    ).select_related('project').first()
    if not scenario_case:
        raise McpActionError(f'场景用例 {scenario_case_id} 不存在或无权限')

    data = arguments.get('data') or {}
    if not isinstance(data, dict) or not data:
        raise McpActionError('data 不能为空，请提供需要更新的字段')

    changed = []
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            raise McpActionError('data.name 不能为空')
        scenario_case.name = name[:200]
        changed.append('name')
    if 'description' in data:
        scenario_case.description = data.get('description') or ''
        changed.append('description')
    if 'folder_id' in data or 'folder_path' in data:
        scenario_case.folder = _resolve_folder_target(
            data.get('folder_id'), data.get('folder_path'),
            scenario_case.project_id, ScenarioCaseFolder,
            field='data.folder_path', label='场景目录',
            create_missing=bool(data.get('create_missing')))
        changed.append('folder')
    if 'environment_id' in data:
        scenario_case.environment = _resolve_environment(
            data.get('environment_id'), scenario_case.project_id)
        changed.append('environment')

    steps = data.get('steps')
    if steps is not None and not isinstance(steps, list):
        raise McpActionError('data.steps 必须是步骤节点数组')

    # 主记录与步骤树同事务提交：步骤校验失败时回滚字段更新。
    with transaction.atomic():
        scenario_case.save()
        steps_replaced = 0
        if steps is not None:
            from apps.api_testing.scenario_service import save_definition
            save_definition(scenario_case, steps, user=user)
            steps_replaced = len(steps)

    result = {
        'scenario_case_id': scenario_case.id,
        'name': scenario_case.name,
        'project_id': scenario_case.project_id,
        'updated_fields': changed,
        'steps_replaced': steps_replaced,
    }
    return result


def update_ui_element_action(arguments: dict, user) -> dict:
    from apps.ui_automation.models import Element, ElementGroup

    element_id = arguments.get('element_id')
    element = Element.objects.filter(
        id=element_id, project__in=accessible_ui_projects(user)
    ).select_related('project').first()
    if not element:
        raise McpActionError(f'元素 {element_id} 不存在或无权限')

    data = arguments.get('data') or {}
    if not isinstance(data, dict) or not data:
        raise McpActionError('data 不能为空，请提供需要更新的字段')

    changed = []
    for field in ('name', 'description', 'page', 'component_name'):
        if field not in data:
            continue
        value = data.get(field)
        if field == 'name':
            value = (value or '').strip()
            if not value:
                raise McpActionError('data.name 不能为空')
            value = value[:200]
        setattr(element, field, '' if value is None else str(value))
        changed.append(field)

    if 'element_type' in data:
        element_type = data.get('element_type')
        if element_type not in dict(Element.ELEMENT_TYPE_CHOICES):
            raise McpActionError(
                f"data.element_type 无效: {element_type}，"
                f"可选: {', '.join(dict(Element.ELEMENT_TYPE_CHOICES))}")
        element.element_type = element_type
        changed.append('element_type')

    if 'locator_strategy' in data or 'locator_strategy_id' in data:
        strategy = _resolve_locator_strategy(
            data.get('locator_strategy') or data.get('locator_strategy_id'))
        element.locator_strategy = strategy
        changed.append('locator_strategy')

    if 'locator_value' in data:
        locator_value = (data.get('locator_value') or '').strip()
        if not locator_value:
            raise McpActionError('data.locator_value 不能为空')
        element.locator_value = locator_value[:500]
        changed.append('locator_value')

    if 'locator_options' in data:
        options = data.get('locator_options') or {}
        if not isinstance(options, dict):
            raise McpActionError('data.locator_options 必须是对象')
        element.locator_options = options
        changed.append('locator_options')

    if 'backup_locators' in data:
        element.backup_locators = _normalize_backup_locators(data.get('backup_locators'))
        changed.append('backup_locators')

    if 'group_id' in data:
        group_id = data.get('group_id')
        if group_id:
            group = ElementGroup.objects.filter(id=group_id, project_id=element.project_id).first()
            if not group:
                raise McpActionError(f'元素分组 {group_id} 不存在或不属于当前项目')
            element.group = group
        else:
            element.group = None
        changed.append('group')

    if 'parent_element_id' in data:
        parent_id = data.get('parent_element_id')
        if parent_id:
            if int(parent_id) == element.id:
                raise McpActionError('父元素不能是元素自身')
            parent = Element.objects.filter(id=parent_id, project_id=element.project_id).first()
            if not parent:
                raise McpActionError(f'父元素 {parent_id} 不存在或不属于当前项目')
            element.parent_element = parent
        else:
            element.parent_element = None
        changed.append('parent_element')

    for field in ('wait_timeout', 'order'):
        if field in data and data.get(field) is not None:
            try:
                setattr(element, field, int(data.get(field)))
            except (TypeError, ValueError):
                raise McpActionError(f'data.{field} 必须是整数') from None
            changed.append(field)

    for field in ('is_visible', 'is_enabled', 'force_action'):
        if field in data:
            setattr(element, field, bool(data.get(field)))
            changed.append(field)

    element.save()

    return {
        'element_id': element.id,
        'name': element.name,
        'project_id': element.project_id,
        'page': element.page,
        'locator_strategy': element.locator_strategy.name if element.locator_strategy else '',
        'locator_value': element.locator_value,
        'updated_fields': changed,
    }


def create_ui_element_action(arguments: dict, user) -> dict:
    """新建 UI 元素；同项目内定位器完全相同的元素直接复用，不重复建库。

    页面未显式指定分组时，按页面名自动挂到同名元素分组（与录制导入、
    元素库 API 的 `resolve_page_group` 行为一致）。
    """
    from apps.ui_automation.models import Element, ElementGroup
    from apps.ui_automation.serializers import resolve_page_group

    project_id = arguments.get('project_id')
    project = accessible_ui_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'UI 项目 {project_id} 不存在或无权限')

    data = arguments.get('data') or {}
    if not isinstance(data, dict):
        raise McpActionError('data 必须是对象')
    name = (data.get('name') or '').strip()
    if not name:
        raise McpActionError('data.name 不能为空')

    element_type = data.get('element_type') or 'BUTTON'
    if element_type not in dict(Element.ELEMENT_TYPE_CHOICES):
        raise McpActionError(
            f"data.element_type 无效: {element_type}，"
            f"可选: {', '.join(dict(Element.ELEMENT_TYPE_CHOICES))}")

    strategy = _resolve_locator_strategy(
        data.get('locator_strategy') or data.get('locator_strategy_id'))
    locator_value = (data.get('locator_value') or '').strip()
    if not locator_value:
        raise McpActionError('data.locator_value 不能为空')
    locator_value = locator_value[:500]

    locator_options = data.get('locator_options') or {}
    if not isinstance(locator_options, dict):
        raise McpActionError('data.locator_options 必须是对象')

    # 定位器三元组完全一致即视为同一元素，直接复用（避免元素库重复膨胀）
    existing = Element.objects.filter(
        project=project, locator_strategy=strategy,
        locator_value=locator_value, locator_options=locator_options,
    ).first()
    if existing:
        return {
            'element_id': existing.id,
            'name': existing.name,
            'project_id': existing.project_id,
            'page': existing.page,
            'group_id': existing.group_id,
            'element_type': existing.element_type,
            'locator_strategy': strategy.name,
            'locator_value': existing.locator_value,
            'validation_status': existing.validation_status,
            'reused': True,
            'warning': _element_validation_warning(existing),
        }

    page = str(data.get('page') or '').strip()
    group = None
    if data.get('group_id'):
        group = ElementGroup.objects.filter(
            id=data.get('group_id'), project=project).first()
        if not group:
            raise McpActionError(
                f'元素分组 {data.get("group_id")} 不存在或不属于当前项目')
        page = group.name
    elif data.get('group_path'):
        # 显式路径优先于 page 的前缀启发式，避免挂错父分组
        group = _resolve_folder_path(
            ElementGroup, project.id, data.get('group_path'),
            field='data.group_path', label='元素分组', parent_field='parent_group',
            create_missing=bool(data.get('create_missing')), name_max_len=200)
        page = group.name
    elif page:
        group = resolve_page_group(project, page)
        if group:
            page = group.name

    backups = _normalize_backup_locators(data.get('backup_locators'))
    wait_timeout = data.get('wait_timeout')
    if wait_timeout is not None:
        try:
            wait_timeout = int(wait_timeout)
        except (TypeError, ValueError):
            raise McpActionError('data.wait_timeout 必须是整数') from None

    element = Element.objects.create(
        project=project,
        group=group,
        name=name[:200],
        description=data.get('description') or '',
        element_type=element_type,
        locator_strategy=strategy,
        locator_value=locator_value,
        locator_options=locator_options,
        backup_locators=backups or None,
        page=page[:200],
        component_name=str(data.get('component_name') or '')[:100],
        order=int(data.get('order') or 0),
        wait_timeout=wait_timeout if wait_timeout is not None else 5,
        is_visible=bool(data.get('is_visible', True)),
        is_enabled=bool(data.get('is_enabled', True)),
        force_action=bool(data.get('force_action', False)),
        created_by=user,
    )
    return {
        'element_id': element.id,
        'name': element.name,
        'project_id': element.project_id,
        'page': element.page,
        'group_id': element.group_id,
        'group_name': element.group.name if element.group else '',
        'element_type': element.element_type,
        'locator_strategy': strategy.name,
        'locator_value': element.locator_value,
        'validation_status': element.validation_status,
        'reused': False,
        'warning': _element_validation_warning(element),
    }


def create_api_request_action(arguments: dict, user) -> dict:
    """在指定集合下新建接口请求；集合不存在时按 collection_name 新建。

    支持 collection_id（ID）、collection_path（「父集合/子集合」路径）或
    collection_name（顶层名称，不存在时自动新建）；请求与集合同事务落库，
    请求校验失败不会留下空集合。
    """
    from django.db import transaction
    from apps.api_testing.models import ApiCollection, ApiRequest

    project_id = arguments.get('project_id')
    project = accessible_api_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'API 项目 {project_id} 不存在或无权限')

    data = arguments.get('data') or {}
    if not isinstance(data, dict):
        raise McpActionError('data 必须是对象')
    name = (data.get('name') or '').strip()
    if not name:
        raise McpActionError('data.name 不能为空')

    method = str(data.get('method') or 'GET').strip().upper()
    if method not in dict(ApiRequest.HTTP_METHODS):
        raise McpActionError(
            f"data.method 无效: {data.get('method')}，"
            f"可选: {', '.join(dict(ApiRequest.HTTP_METHODS))}")
    url = str(data.get('url') or '').strip()
    if not url:
        raise McpActionError('data.url 不能为空')

    request_type = data.get('request_type') or (
        'WEBSOCKET' if project.project_type == 'WEBSOCKET' else 'HTTP')
    if request_type not in dict(ApiRequest.REQUEST_TYPE_CHOICES):
        raise McpActionError(
            f"data.request_type 无效: {request_type}，"
            f"可选: {', '.join(dict(ApiRequest.REQUEST_TYPE_CHOICES))}")

    json_objects = {}
    for field in ('headers', 'params', 'body', 'auth'):
        value = data.get(field)
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise McpActionError(f'data.{field} 必须是对象')
        json_objects[field] = value

    json_lists = {}
    for field in ('assertions', 'extractors'):
        value = data.get(field)
        if value is None:
            value = []
        if not isinstance(value, (list, tuple)):
            raise McpActionError(f'data.{field} 必须是数组')
        json_lists[field] = list(value)

    # 同项目同 method + url 的既有请求作为「可能重复」提示返回，不自动合并：
    # 同一接口不同入参（body/params）是常见场景，自动复用会丢失区分度。
    similar = list(
        ApiRequest.objects.filter(collection__project=project, method=method, url=url)
        .values('id', 'name')[:5])

    with transaction.atomic():
        if data.get('collection_path') and not data.get('collection_id'):
            # 显式路径：支持「父集合/子集合」，路径本身消除同名歧义
            collection = _resolve_folder_path(
                ApiCollection, project.id, data.get('collection_path'),
                field='data.collection_path', label='接口集合',
                create_missing=bool(data.get('create_missing')), name_max_len=200)
            collection_created = False
        else:
            collection, collection_created = _resolve_collection(
                project, data.get('collection_id'), data.get('collection_name'))
        api_request = ApiRequest.objects.create(
            collection=collection,
            name=name[:200],
            description=data.get('description') or '',
            request_type=request_type,
            method=method,
            url=url,
            headers=json_objects['headers'],
            params=json_objects['params'],
            body=json_objects['body'],
            auth=json_objects['auth'],
            pre_request_script=data.get('pre_request_script') or '',
            post_request_script=data.get('post_request_script') or '',
            assertions=json_lists['assertions'],
            extractors=json_lists['extractors'],
            order=int(data.get('order') or 0),
            created_by=user,
        )

    return {
        'request_id': api_request.id,
        'name': api_request.name,
        'project_id': project.id,
        'collection_id': collection.id,
        'collection_name': collection.name,
        'collection_created': collection_created,
        'method': api_request.method,
        'url': api_request.url,
        'request_type': api_request.request_type,
        'similar_requests': similar,
    }


def import_recorded_script_action(arguments: dict, user) -> dict:
    """Persist a reviewed Playwright recording in an accessible UI project."""
    from apps.ui_automation.recording_import import import_recorded_script
    from apps.ui_automation.script_security import ScriptSecurityError

    project_id = arguments.get('project_id')
    project = accessible_ui_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'UI 项目 {project_id} 不存在或无权限')
    try:
        script, analysis = import_recorded_script(
            project=project,
            user=user,
            content=arguments.get('content'),
            name=arguments.get('name', ''),
            start_url=arguments.get('start_url', ''),
            browser=arguments.get('browser', 'chromium'),
            device=arguments.get('device', ''),
            save_storage=arguments.get('save_storage', False),
            expected_content_hash=arguments.get('content_hash', ''),
        )
    except ScriptSecurityError as exc:
        raise McpActionError(str(exc)) from exc
    return {
        'script_id': script.id,
        'project_id': project.id,
        'project_name': project.name,
        'name': script.name,
        'source': script.source,
        'language': script.language,
        'framework': script.framework,
        'content_hash': analysis['content_hash'],
        'locator_count': analysis['locator_count'],
    }


# --------------------------------------------------------------------- #
# 测试计划 / 用例评审
#   _resolve_*：校验并归一化参数、算出影响面。preview 阶段先跑一遍
#   （validate_preview），confirm 阶段再跑一遍后落库，两处规则一致。
#   落库一律走各域的 services / 序列化器，与页面接口共用同一套写入逻辑。
# --------------------------------------------------------------------- #

def _data_dict(arguments, allowed):
    """取 arguments.data：必须是非空对象，且不含未知字段。"""
    data = arguments.get('data')
    if not isinstance(data, dict):
        raise McpActionError('data 必须是对象')
    if not data:
        raise McpActionError('data 不能为空，请提供需要写入的字段')
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise McpActionError(
            f'data 不支持的字段: {", ".join(unknown)}（支持: {", ".join(sorted(allowed))}）')
    return data


def _id_list(value, label, allow_empty=True):
    """校验 ID 数组并去重（保持顺序）；不接受 null、字符串或非整数元素。"""
    if not isinstance(value, list):
        raise McpActionError(f'{label} 必须是 ID 数组')
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise McpActionError(f'{label} 只能包含整数 ID')
    ids = list(dict.fromkeys(value))
    if not ids and not allow_empty:
        raise McpActionError(f'{label} 至少包含一个 ID')
    return ids


def _fmt_ids(ids):
    return ', '.join(str(item) for item in ids)


def _accessible_project_ids(user, project_ids):
    return set(accessible_projects(user).filter(id__in=project_ids).values_list('id', flat=True))


def _check_project_changes(user, current_ids, new_ids, label='data.projects'):
    """新增或移除的项目都要求当前用户可访问，原样保留的不重复校验。

    返回 (新增的项目 ID 列表, 移除的项目 ID 列表)。
    """
    current, new = set(current_ids), set(new_ids)
    added = [project_id for project_id in new_ids if project_id not in current]
    removed = sorted(current - new)
    touched = set(added) | set(removed)
    denied = sorted(touched - _accessible_project_ids(user, touched))
    if denied:
        raise McpActionError(f'{label} 涉及不存在或无权限的项目: {_fmt_ids(denied)}')
    return added, removed


def _require_active_users(user_ids, label):
    from django.contrib.auth import get_user_model
    found = set(get_user_model().objects.filter(
        id__in=user_ids, is_active=True).values_list('id', flat=True))
    missing = [user_id for user_id in user_ids if user_id not in found]
    if missing:
        raise McpActionError(f'{label} 包含不存在或已停用的用户: {_fmt_ids(missing)}')


def _project_names(project_ids):
    from apps.projects.models import Project
    names = dict(Project.objects.filter(id__in=project_ids).values_list('id', 'name'))
    return '、'.join(f'「{names.get(project_id, project_id)}」' for project_id in project_ids)


_PLAN_CREATE_FIELDS = {'name', 'description', 'is_active', 'version',
                       'projects', 'assignees', 'testcases'}
_PLAN_UPDATE_FIELDS = _PLAN_CREATE_FIELDS | {'add_testcases', 'remove_testcases'}


def _plan_scalar_fields(data, creating):
    """校验 name / description / is_active，返回可直接交给 services 的普通字段。"""
    fields = {}
    if creating or 'name' in data:
        name = data.get('name')
        if not isinstance(name, str) or not name.strip():
            raise McpActionError('data.name 不能为空')
        if len(name.strip()) > 200:
            raise McpActionError('data.name 不能超过 200 个字符')
        fields['name'] = name.strip()
    if 'description' in data:
        description = data['description']
        if description is not None and not isinstance(description, str):
            raise McpActionError('data.description 必须是字符串')
        fields['description'] = description or ''
    if 'is_active' in data:
        if not isinstance(data['is_active'], bool):
            raise McpActionError('data.is_active 必须是布尔值 true / false')
        fields['is_active'] = data['is_active']
    return fields


def _resolve_plan_version(version_id, user, current_id=None):
    """版本须存在且属于当前用户可访问的项目；null / 0 表示不关联版本。"""
    from apps.versions.models import Version

    if version_id in (None, 0):
        return None
    if isinstance(version_id, bool) or not isinstance(version_id, int):
        raise McpActionError('data.version 必须是版本 ID 或 null')
    queryset = Version.objects.filter(id=version_id)
    if version_id != current_id:
        queryset = queryset.filter(projects__in=accessible_projects(user))
    version = queryset.first()
    if not version:
        raise McpActionError(f'版本 {version_id} 不存在或无权限')
    return version


def _case_project_map(testcase_ids):
    """{用例 ID: 所属项目 ID}，只包含真实存在的用例。"""
    from apps.testcases.models import TestCase
    return dict(TestCase.objects.filter(id__in=testcase_ids).values_list('id', 'project_id'))


def _resolve_test_plan_create(arguments: dict, user) -> dict:
    data = _data_dict(arguments, _PLAN_CREATE_FIELDS)
    fields = _plan_scalar_fields(data, creating=True)

    if 'projects' not in data:
        raise McpActionError('data.projects 必填：至少关联一个项目')
    project_ids = _id_list(data['projects'], 'data.projects', allow_empty=False)
    _check_project_changes(user, [], project_ids)

    testcase_ids = _id_list(data.get('testcases') or [], 'data.testcases')
    case_projects = _case_project_map(testcase_ids)
    invalid = [case_id for case_id in testcase_ids
               if case_projects.get(case_id) not in set(project_ids)]
    if invalid:
        raise McpActionError(f'data.testcases 包含不存在或不属于所选项目的用例: {_fmt_ids(invalid)}')

    assignee_ids = _id_list(data.get('assignees') or [], 'data.assignees')
    _require_active_users(assignee_ids, 'data.assignees')

    return {
        'fields': fields,
        'project_ids': project_ids,
        'testcase_ids': testcase_ids,
        'assignee_ids': assignee_ids,
        'version': _resolve_plan_version(data.get('version'), user),
    }


def create_test_plan_action(arguments: dict, user) -> dict:
    """创建测试计划并生成各项目的执行记录（计划、执行记录、执行用例同事务落库）。"""
    from apps.executions import services
    from apps.executions.models import TestRun

    resolved = _resolve_test_plan_create(arguments, user)
    version = resolved['version']
    plan = services.create_test_plan(
        creator=user,
        fields=resolved['fields'],
        version_id=version.id if version else None,
        assignee_ids=resolved['assignee_ids'],
        project_ids=resolved['project_ids'],
        testcase_ids=resolved['testcase_ids'],
    )
    return {
        'plan_id': plan.id,
        'name': plan.name,
        'is_active': plan.is_active,
        'version_id': plan.version_id,
        'project_ids': resolved['project_ids'],
        'assignee_ids': resolved['assignee_ids'],
        'test_run_ids': list(
            TestRun.objects.filter(test_plan=plan).order_by('id').values_list('id', flat=True)),
        'testcase_count': len(resolved['testcase_ids']),
    }


def _resolve_test_plan_update(arguments: dict, user, lock=False):
    """返回 (plan, 交给 services.update_test_plan 的参数, 影响面)。"""
    from apps.executions import services
    from apps.executions.models import TestPlan, TestRunCase

    plan_id = arguments.get('plan_id')
    plan = services.accessible_test_plans(user).filter(id=plan_id).first()
    if not plan:
        raise McpActionError(f'测试计划 {plan_id} 不存在或无权限')
    if lock:
        plan = TestPlan.objects.select_for_update().get(id=plan.id)

    data = _data_dict(arguments, _PLAN_UPDATE_FIELDS)
    changes = {'fields': _plan_scalar_fields(data, creating=False)}

    if 'version' in data:
        version = _resolve_plan_version(data['version'], user, current_id=plan.version_id)
        changes['version_id'] = version.id if version else None

    if 'assignees' in data:
        assignee_ids = _id_list(data['assignees'], 'data.assignees')
        current_assignees = set(plan.assignees.values_list('id', flat=True))
        _require_active_users(
            [user_id for user_id in assignee_ids if user_id not in current_assignees],
            'data.assignees')
        changes['assignee_ids'] = assignee_ids

    project_ids = list(plan.projects.values_list('id', flat=True))
    projects_added, projects_removed = [], []
    if 'projects' in data:
        new_project_ids = _id_list(data['projects'], 'data.projects', allow_empty=False)
        projects_added, projects_removed = _check_project_changes(
            user, project_ids, new_project_ids)
        project_ids = changes['project_ids'] = new_project_ids

    # 当前执行用例：(用例 ID, 执行状态, 所在执行记录的项目 ID)
    run_cases = list(TestRunCase.objects.filter(test_run__test_plan=plan).values_list(
        'testcase_id', 'status', 'test_run__project_id'))
    current_case_ids = list(dict.fromkeys(case_id for case_id, _, _ in run_cases))

    incremental = [key for key in ('add_testcases', 'remove_testcases') if key in data]
    final_case_ids = None
    if 'testcases' in data:
        if incremental:
            raise McpActionError(
                'data.testcases（全量替换）不能与 add_testcases / remove_testcases（增量）同时使用')
        final_case_ids = _id_list(data['testcases'], 'data.testcases')
    elif incremental:
        add_ids = _id_list(data.get('add_testcases', []), 'data.add_testcases')
        remove_ids = _id_list(data.get('remove_testcases', []), 'data.remove_testcases')
        overlap = sorted(set(add_ids) & set(remove_ids))
        if overlap:
            raise McpActionError(f'同一用例不能既新增又移除: {_fmt_ids(overlap)}')
        not_in_plan = [case_id for case_id in remove_ids if case_id not in current_case_ids]
        if not_in_plan:
            raise McpActionError(f'data.remove_testcases 包含不在计划中的用例: {_fmt_ids(not_in_plan)}')
        final_case_ids = ([case_id for case_id in current_case_ids if case_id not in remove_ids]
                          + [case_id for case_id in add_ids if case_id not in current_case_ids])
    if final_case_ids is not None:
        changes['testcase_ids'] = final_case_ids

    impact = {'projects_added': projects_added, 'projects_removed': projects_removed,
              'testcases_added': 0, 'testcases_removed': 0, 'results_removed': 0}
    if 'project_ids' in changes or 'testcase_ids' in changes:
        # 与 services.sync_test_runs 的取舍一致：执行用例留下的条件是所在项目仍在计划里、
        # 用例仍在名单里，且用例没有被移到别的项目
        project_set = set(project_ids)
        kept_case_ids = set(current_case_ids if final_case_ids is None else final_case_ids)
        case_projects = _case_project_map(kept_case_ids | set(current_case_ids))
        added = [case_id for case_id in (final_case_ids or []) if case_id not in current_case_ids]
        invalid = [case_id for case_id in added if case_projects.get(case_id) not in project_set]
        if invalid:
            raise McpActionError(f'新增用例不存在或不属于计划关联项目: {_fmt_ids(invalid)}')
        removed = [
            (case_id, status, run_project_id) for case_id, status, run_project_id in run_cases
            if run_project_id not in project_set or case_id not in kept_case_ids
            or case_projects.get(case_id) != run_project_id
        ]
        # 被整体移出的项目已在上面校验过；这里再校验增删用例所触及的项目
        touched = ({case_projects[case_id] for case_id in added}
                   | {run_project_id for _, _, run_project_id in removed})
        denied = sorted(touched - _accessible_project_ids(user, touched))
        if denied:
            raise McpActionError(f'无权调整项目 {_fmt_ids(denied)} 下的执行用例')
        impact.update(
            testcases_added=len(added),
            testcases_removed=len(removed),
            results_removed=sum(1 for _, status, _ in removed if status != 'untested'),
        )
    return plan, changes, impact


def update_test_plan_action(arguments: dict, user) -> dict:
    """更新测试计划：只改传入的字段，校验与落库在同一事务内完成。"""
    from django.db import transaction
    from apps.executions import services

    with transaction.atomic():
        plan, changes, impact = _resolve_test_plan_update(arguments, user, lock=True)
        services.update_test_plan(plan, **changes)
    return {
        'plan_id': plan.id,
        'name': plan.name,
        'updated_fields': sorted(arguments.get('data') or {}),
        **impact,
    }


def _accessible_review(review_id, user):
    from apps.reviews.services import accessible_reviews
    review = accessible_reviews(user).filter(id=review_id).first()
    if not review:
        raise McpActionError(f'评审 {review_id} 不存在或无权限')
    return review


def _flatten_errors(errors) -> str:
    """把 DRF 序列化器的校验错误压成一行，供 Agent 阅读。"""
    if isinstance(errors, dict):
        return '；'.join(f'{key}: {_flatten_errors(value)}' for key, value in errors.items())
    if isinstance(errors, (list, tuple)):
        return '、'.join(_flatten_errors(item) for item in errors)
    return str(errors)


_REVIEW_FIELDS = {'title', 'description', 'priority', 'deadline',
                  'projects', 'testcases', 'reviewers', 'template'}
_COMMENT_UPDATE_FIELDS = {'content', 'comment_type', 'testcase', 'step_number', 'is_resolved'}
_COMMENT_TYPE_LABELS = {'general': '整体意见', 'testcase': '用例意见', 'step': '步骤意见'}


def _review_serializer(payload, user, review=None):
    """复用页面序列化器，统一校验字段、模板启用状态和项目归属。"""
    from apps.reviews.serializers import TestCaseReviewCreateSerializer

    serializer = TestCaseReviewCreateSerializer(
        review, data=payload, partial=review is not None, context={'user': user})
    if not serializer.is_valid():
        raise McpActionError(f'data 校验失败：{_flatten_errors(serializer.errors)}')
    return serializer


def _resolve_review_create(arguments: dict, user):
    payload = dict(_data_dict(arguments, _REVIEW_FIELDS))
    for field in ('projects', 'testcases', 'reviewers'):
        payload[field] = _id_list(payload.get(field), f'data.{field}', allow_empty=False)

    # 先确认项目可访问，再限制用例只能来自本次评审选中的项目。
    _check_project_changes(user, [], payload['projects'])
    selected_projects = set(payload['projects'])
    case_projects = _case_project_map(payload['testcases'])
    invalid = [pk for pk in payload['testcases'] if case_projects.get(pk) not in selected_projects]
    if invalid:
        raise McpActionError(f'data.testcases 包含不存在或不属于所选项目的用例: {_fmt_ids(invalid)}')
    _require_active_users(payload['reviewers'], 'data.reviewers')
    return _review_serializer(payload, user)


def create_review_action(arguments: dict, user) -> dict:
    serializer = _resolve_review_create(arguments, user)
    review = serializer.save(creator=user)
    data = serializer.validated_data
    return {
        'review_id': review.id, 'title': review.title, 'status': review.status,
        'project_ids': data['projects'], 'testcase_count': len(data['testcases']),
        'reviewer_ids': data['reviewers'],
    }


def _resolve_review_update(arguments: dict, user):
    """返回 (review, 已通过校验的序列化器, 影响面)；落库由序列化器转给 reviews.services。"""
    from apps.reviews import services
    from apps.reviews.models import ReviewAssignment
    from apps.testcases.models import TestCase

    review = _accessible_review(arguments.get('review_id'), user)
    if review.creator_id != user.id:
        raise McpActionError('只有评审创建人可以修改评审')
    if review.status not in services.OPEN_STATUSES:
        raise McpActionError(f'评审已结束（{review.get_status_display()}），不能再修改')

    data = _data_dict(arguments, _REVIEW_FIELDS)
    payload = dict(data)
    impact = {}

    project_ids = list(review.projects.values_list('id', flat=True))
    if 'projects' in data:
        new_project_ids = _id_list(data['projects'], 'data.projects', allow_empty=False)
        impact['projects_added'], impact['projects_removed'] = _check_project_changes(
            user, project_ids, new_project_ids)
        project_ids = payload['projects'] = new_project_ids

    if 'testcases' in data:
        testcase_ids = _id_list(data['testcases'], 'data.testcases', allow_empty=False)
        current = set(review.testcases.values_list('id', flat=True))
        added = [case_id for case_id in testcase_ids if case_id not in current]
        # 新增的用例须属于评审关联项目，且当前用户对该项目有访问权限；已在评审里的不重复校验
        allowed_projects = _accessible_project_ids(user, project_ids)
        valid = set(TestCase.objects.filter(
            id__in=added, project_id__in=allowed_projects).values_list('id', flat=True))
        invalid = [case_id for case_id in added if case_id not in valid]
        if invalid:
            raise McpActionError(
                f'data.testcases 新增的用例不存在，或不属于评审关联且你有权限的项目: {_fmt_ids(invalid)}')
        payload['testcases'] = testcase_ids
        impact['testcases_added'] = len(added)
        impact['testcases_removed'] = len(current - set(testcase_ids))

    if 'reviewers' in data:
        reviewer_ids = _id_list(data['reviewers'], 'data.reviewers', allow_empty=False)
        assignments = dict(ReviewAssignment.objects.filter(
            review=review).values_list('reviewer_id', 'status'))
        added = [user_id for user_id in reviewer_ids if user_id not in assignments]
        _require_active_users(added, 'data.reviewers')
        removed = [user_id for user_id in assignments if user_id not in reviewer_ids]
        payload['reviewers'] = reviewer_ids
        impact['reviewers_added'] = added
        impact['reviewers_removed'] = removed
        impact['verdicts_removed'] = sum(
            1 for user_id in removed if assignments[user_id] != 'pending')

    return review, _review_serializer(payload, user, review), impact


def update_review_action(arguments: dict, user) -> dict:
    """修改评审：字段与关联经评审序列化器校验后由 reviews.services 同事务落库。"""
    review, serializer, impact = _resolve_review_update(arguments, user)
    serializer.save()
    return {
        'review_id': review.id,
        'title': review.title,
        'updated_fields': sorted(arguments.get('data') or {}),
        **impact,
    }


def _resolve_review_comment_create(arguments: dict, user):
    """返回 (review, 已通过校验的序列化器)；意见规则由评审意见序列化器统一校验。"""
    from apps.reviews.serializers import TestCaseReviewCommentCreateSerializer

    review = _accessible_review(arguments.get('review_id'), user)
    serializer = TestCaseReviewCommentCreateSerializer(data={
        'review': review.id,
        'comment_type': str(arguments.get('comment_type') or 'general').strip().lower(),
        'content': arguments.get('content'),
        'testcase': arguments.get('testcase_id') or None,
        'step_number': arguments.get('step_number') or None,
    })
    if not serializer.is_valid():
        raise McpActionError(f'评审意见校验失败：{_flatten_errors(serializer.errors)}')
    return review, serializer


def add_review_comment_action(arguments: dict, user) -> dict:
    review, serializer = _resolve_review_comment_create(arguments, user)
    comment = serializer.save(author=user)
    return {
        'comment_id': comment.id,
        'review_id': review.id,
        'comment_type': comment.comment_type,
        'testcase_id': comment.testcase_id,
        'step_number': comment.step_number,
    }


def _resolve_review_comment_update(arguments: dict, user):
    """返回 (comment, 内容字段的序列化器或 None, 新的解决状态或 None)。

    内容字段（content / comment_type / testcase / step_number）只有作者能改；
    解决状态（is_resolved）作者和评审创建人都能改。评审结束后两者都冻结。
    """
    from apps.reviews import services
    from apps.reviews.models import TestCaseReviewComment
    from apps.reviews.serializers import TestCaseReviewCommentCreateSerializer

    comment_id = arguments.get('comment_id')
    comment = TestCaseReviewComment.objects.filter(
        id=comment_id, review__in=services.accessible_reviews(user)
    ).select_related('review', 'testcase').first()
    if not comment:
        raise McpActionError(f'评审意见 {comment_id} 不存在或无权限')

    payload = dict(_data_dict(arguments, _COMMENT_UPDATE_FIELDS))
    is_resolved = None
    if 'is_resolved' in payload:
        is_resolved = payload.pop('is_resolved')
        if not isinstance(is_resolved, bool):
            raise McpActionError('data.is_resolved 必须是布尔值 true / false')
        try:
            services.check_comment_status_changeable(comment, user)
        except services.ReviewRuleError as exc:
            raise McpActionError(str(exc)) from exc

    serializer = None
    if payload:
        if not services.is_comment_author(comment, user):
            raise McpActionError('只能修改本人发布的评审意见（评审创建人只能修改意见的解决状态）')
        if isinstance(payload.get('comment_type'), str):
            payload['comment_type'] = payload['comment_type'].strip().lower()
        # 与 preview_add_review_comment 的入参约定一致：0 等同于不关联用例 / 不指定步骤
        for key in ('testcase', 'step_number'):
            if key in payload and not payload[key]:
                payload[key] = None
        serializer = TestCaseReviewCommentCreateSerializer(comment, data=payload, partial=True)
        if not serializer.is_valid():
            raise McpActionError(f'评审意见校验失败：{_flatten_errors(serializer.errors)}')
    return comment, serializer, is_resolved


def update_review_comment_action(arguments: dict, user) -> dict:
    from django.db import transaction
    from apps.reviews import services

    comment, serializer, is_resolved = _resolve_review_comment_update(arguments, user)
    with transaction.atomic():
        if serializer is not None:
            serializer.save()
        if is_resolved is not None:
            try:
                services.set_comment_resolved(comment, user, is_resolved)
            except services.ReviewRuleError as exc:
                raise McpActionError(str(exc)) from exc
    return {
        'comment_id': comment.id,
        'review_id': comment.review_id,
        'updated_fields': sorted(arguments.get('data') or {}),
        'comment_type': comment.comment_type,
        'testcase_id': comment.testcase_id,
        'step_number': comment.step_number,
        'is_resolved': comment.is_resolved,
    }


#: preview 阶段先跑一遍校验的操作：参数或权限有问题时直接报错，不生成待确认记录
PREVIEW_VALIDATORS = {
    'confirm_create_test_plan': _resolve_test_plan_create,
    'confirm_update_test_plan': _resolve_test_plan_update,
    'confirm_create_review': _resolve_review_create,
    'confirm_update_review': _resolve_review_update,
    'confirm_add_review_comment': _resolve_review_comment_create,
    'confirm_update_review_comment': _resolve_review_comment_update,
}


def validate_preview(tool_name: str, arguments: dict, user):
    """preview_* 工具在生成待确认记录前调用；校验失败抛 McpActionError。"""
    validator = PREVIEW_VALIDATORS.get(tool_name)
    if validator is not None:
        validator(arguments, user)


def _preview_create_test_plan(arguments, user) -> str:
    resolved = _resolve_test_plan_create(arguments, user)
    parts = [f'关联项目 {_project_names(resolved["project_ids"])}',
             f'{len(resolved["testcase_ids"])} 条用例']
    if resolved['assignee_ids']:
        parts.append(f'指派给 {len(resolved["assignee_ids"])} 人')
    if resolved['version']:
        parts.append(f'版本「{resolved["version"].name}」')
    return f'将创建测试计划「{resolved["fields"]["name"]}」（{"，".join(parts)}）'


def _preview_update_test_plan(arguments, user) -> str:
    plan, _, impact = _resolve_test_plan_update(arguments, user)
    notes = []
    if impact['projects_added']:
        notes.append(f'新增项目 {_project_names(impact["projects_added"])}')
    if impact['projects_removed']:
        notes.append(f'移除项目 {_project_names(impact["projects_removed"])} 及其执行记录')
    if impact['testcases_added']:
        notes.append(f'新增 {impact["testcases_added"]} 条执行用例')
    if impact['testcases_removed']:
        note = f'移除 {impact["testcases_removed"]} 条执行用例'
        if impact['results_removed']:
            note += f'（其中 {impact["results_removed"]} 条已有执行结果，结果与执行历史将一并删除）'
        notes.append(note)
    return (f'将更新测试计划「{plan.name}」（ID {plan.id}）：'
            f'字段 {", ".join(sorted(arguments.get("data") or {}))}'
            + ''.join(f'；{note}' for note in notes))


def _preview_create_review(arguments, user) -> str:
    data = _resolve_review_create(arguments, user).validated_data
    return (f'将创建评审「{data["title"]}」，关联项目 {_project_names(data["projects"])}，'
            f'{len(data["testcases"])} 条用例，指派 {len(data["reviewers"])} 位评审人')


def _preview_update_review(arguments, user) -> str:
    review, _, impact = _resolve_review_update(arguments, user)
    notes = []
    if impact.get('projects_added'):
        notes.append(f'新增项目 {_project_names(impact["projects_added"])}')
    if impact.get('projects_removed'):
        notes.append(f'移除项目 {_project_names(impact["projects_removed"])}')
    if impact.get('testcases_added') or impact.get('testcases_removed'):
        notes.append(f'评审用例新增 {impact["testcases_added"]} 条、'
                     f'移除 {impact["testcases_removed"]} 条')
    if impact.get('reviewers_added'):
        notes.append(f'新增 {len(impact["reviewers_added"])} 位评审人')
    if impact.get('reviewers_removed'):
        note = f'移除 {len(impact["reviewers_removed"])} 位评审人'
        if impact['verdicts_removed']:
            note += f'（其中 {impact["verdicts_removed"]} 位已提交的评审结论将一并删除）'
        notes.append(note)
    return (f'将更新评审「{review.title}」（ID {review.id}）：'
            f'字段 {", ".join(sorted(arguments.get("data") or {}))}'
            + ''.join(f'；{note}' for note in notes))


def _comment_excerpt(content) -> str:
    text = ' '.join(str(content or '').split())
    return text if len(text) <= 60 else f'{text[:60]}…'


def _preview_add_review_comment(arguments, user) -> str:
    review, serializer = _resolve_review_comment_create(arguments, user)
    validated = serializer.validated_data
    label = _COMMENT_TYPE_LABELS.get(validated.get('comment_type', 'general'), '评审意见')
    testcase = validated.get('testcase')
    target = f'（用例 #{testcase.id}「{testcase.title}」）' if testcase else ''
    return (f'将在评审「{review.title}」（ID {review.id}）添加{label}{target}：'
            f'{_comment_excerpt(validated.get("content"))}')


def _preview_update_review_comment(arguments, user) -> str:
    comment, _, is_resolved = _resolve_review_comment_update(arguments, user)
    fields = sorted(set(arguments.get('data') or {}) - {'is_resolved'})
    notes = []
    if fields:
        notes.append(f'字段 {", ".join(fields)}')
    if is_resolved is not None:
        notes.append(f'标记为{"已解决" if is_resolved else "未解决"}')
    return (f'将修改评审「{comment.review.title}」下的评审意见 {comment.id}：'
            + '；'.join(notes))


#: confirm 分发注册表：tool_name → (执行体, preview 构造函数)
def build_preview(tool_name: str, arguments: dict, user) -> str:
    """构造 preview 文本；无法预览时给出通用描述。"""
    try:
        if tool_name == 'confirm_run_api_suite':
            from apps.api_testing.models import TestSuite, TestSuiteItem
            suite = TestSuite.objects.filter(id=arguments.get('suite_id')).first()
            count = TestSuiteItem.objects.filter(
                test_suite=suite, enabled=True).count() if suite else 0
            environment_id = arguments.get('environment_id')
            return (f'将执行 API 测试套件「{suite.name if suite else arguments.get("suite_id")}」'
                    f'（{count} 个场景用例'
                    + (f'，执行环境 {environment_id}' if environment_id else '')
                    + '）')
        if tool_name == 'confirm_run_api_scenario':
            from apps.api_testing.models import Environment, ScenarioCase
            scenario_case = ScenarioCase.objects.filter(
                id=arguments.get('scenario_case_id')).first()
            steps = (list(scenario_case.steps.filter(enabled=True, type='http'))
                     if scenario_case else [])
            count = len(steps)
            write_count = sum(
                step.config.get('request', {}).get('method') in {'POST', 'PUT', 'PATCH', 'DELETE'}
                for step in steps
            )
            environment_id = arguments.get('environment_id') or (
                scenario_case.environment_id if scenario_case else None)
            environment = (Environment.objects.filter(id=environment_id).first()
                           if environment_id else None)
            environment_note = (f'执行环境「{environment.name}」'
                                if environment else '未绑定执行环境（使用请求内地址）')
            return (f'将执行接口场景用例'
                    f'「{scenario_case.name if scenario_case else arguments.get("scenario_case_id")}」'
                    f'（{count} 个请求'
                    + (f'，其中 {write_count} 个写请求 POST/PUT/PATCH/DELETE'
                       if write_count else '')
                    + f'；{environment_note}；会真实发起 HTTP 调用）')
        if tool_name == 'confirm_run_ui_case':
            from apps.ui_automation.models import TestCase as UiCase
            case = UiCase.objects.filter(id=arguments.get('case_id')).first()
            return (f'将以 {arguments.get("engine", "playwright")} 无头模式执行 UI 用例'
                    f'「{case.name if case else arguments.get("case_id")}」')
        if tool_name == 'confirm_run_ui_suite':
            from apps.ui_automation.models import TestSuite
            suite = TestSuite.objects.filter(id=arguments.get('suite_id')).first()
            count = suite.suite_test_cases.count() if suite else 0
            return (f'将以 {arguments.get("engine", "playwright")}/'
                    f'{arguments.get("browser", "chrome")} 执行 UI 套件'
                    f'「{suite.name if suite else arguments.get("suite_id")}」（{count} 个用例）')
        if tool_name == 'confirm_run_perf_scene':
            from apps.perf_testing.models import PerfScenario
            scene = PerfScenario.objects.filter(id=arguments.get('scene_id')).first()
            load = (scene.get_load_config() if scene else {}) or {}
            return (f'将对压测场景「{scene.name if scene else arguments.get("scene_id")}」发起压测'
                    f'（并发 {load.get("concurrency", "?")}，时长 {load.get("duration", "?")}s）')
        if tool_name == 'confirm_create_testcase':
            data = arguments.get('data') or {}
            return f'将在项目 {arguments.get("project_id")} 创建测试用例「{data.get("title", "未命名")}」'
        if tool_name == 'confirm_update_testcase':
            data = arguments.get('data') or {}
            return (f'将更新功能测试用例 {arguments.get("testcase_id")}：'
                    f'字段 {", ".join(sorted(data)) or "（空）"}')
        if tool_name == 'confirm_create_ui_case':
            from apps.ui_automation.models import UiProject
            data = arguments.get('data') or {}
            project = UiProject.objects.filter(id=arguments.get('project_id')).first()
            steps = data.get('steps') or []
            link = arguments.get('test_case_id')
            return (f'将在 UI 项目「{project.name if project else arguments.get("project_id")}」'
                    f'新建 UI 自动化用例「{data.get("name", "未命名")}」（{len(steps)} 个步骤）'
                    + _link_preview_note(link, 'ui_test_case',
                                         bool(arguments.get('replace_link'))))
        if tool_name == 'confirm_create_api_scenario_case':
            from apps.api_testing.models import ApiProject
            data = arguments.get('data') or {}
            project = ApiProject.objects.filter(id=arguments.get('project_id')).first()
            steps = data.get('steps') or []
            link = arguments.get('test_case_id')
            enabled = sum(1 for item in steps
                          if isinstance(item, dict) and item.get('enabled', True))
            return (f'将在 API 项目「{project.name if project else arguments.get("project_id")}」'
                    f'新建场景用例「{data.get("name", "未命名")}」（{len(steps)} 个步骤，'
                    f'其中 {enabled} 个启用）'
                    + _link_preview_note(link, 'api_scenario_case',
                                         bool(arguments.get('replace_link'))))
        if tool_name == 'confirm_update_ui_case':
            data = arguments.get('data') or {}
            steps = data.get('steps')
            return (f'将更新 UI 自动化用例 {arguments.get("case_id")}：'
                    f'字段 {", ".join(sorted(data)) or "（空）"}'
                    + (f'，并用 {len(steps)} 个步骤整体覆盖原步骤' if isinstance(steps, list) else ''))
        if tool_name == 'confirm_update_api_scenario_case':
            data = arguments.get('data') or {}
            steps = data.get('steps')
            return (f'将更新接口场景用例 {arguments.get("scenario_case_id")}：'
                    f'字段 {", ".join(sorted(data)) or "（空）"}'
                    + (f'，并用 {len(steps)} 个步骤整体覆盖原步骤树' if isinstance(steps, list) else ''))
        if tool_name == 'confirm_update_ui_element':
            from apps.ui_automation.models import Element
            data = arguments.get('data') or {}
            element = Element.objects.filter(id=arguments.get('element_id')).first()
            return (f'将更新元素「{element.name if element else arguments.get("element_id")}」'
                    f'（ID {arguments.get("element_id")}）：字段 {", ".join(sorted(data)) or "（空）"}')
        if tool_name == 'confirm_create_ui_element':
            from apps.ui_automation.models import UiProject
            data = arguments.get('data') or {}
            project = UiProject.objects.filter(id=arguments.get('project_id')).first()
            return (f'将在 UI 项目「{project.name if project else arguments.get("project_id")}」'
                    f'新建元素「{data.get("name", "未命名")}」'
                    f'（页面 {data.get("page") or "未指定"}，'
                    f'定位 {data.get("locator_strategy") or "?"} / {data.get("locator_value") or "?"}）')
        if tool_name == 'confirm_create_api_request':
            from apps.api_testing.models import ApiCollection, ApiProject
            data = arguments.get('data') or {}
            project = ApiProject.objects.filter(id=arguments.get('project_id')).first()
            collection_id = data.get('collection_id')
            collection_name = str(data.get('collection_name') or '').strip()
            collection = (ApiCollection.objects.filter(id=collection_id).first()
                          if collection_id else None)
            if collection is None and collection_name and project:
                collection = ApiCollection.objects.filter(
                    project=project, name=collection_name).first()
            if collection is not None:
                collection_note = f'集合「{collection.name}」'
            elif collection_name:
                collection_note = f'集合「{collection_name}」不存在，将新建'
            else:
                collection_note = f'集合 {collection_id}'
            return (f'将在 API 项目「{project.name if project else arguments.get("project_id")}」'
                    f'新建接口「{str(data.get("method") or "GET").upper()} '
                    f'{data.get("url") or "?"}」（{collection_note}）')
        if tool_name == 'confirm_import_recorded_script':
            from apps.ui_automation.models import UiProject
            project = UiProject.objects.filter(id=arguments.get('project_id')).first()
            name = str(arguments.get('name') or 'testhub_recorded.py')[:200]
            content = arguments.get('content') or ''
            return (f'将在 UI 项目「{project.name if project else arguments.get("project_id")}」'
                    f'导入录制脚本「{name}」（{len(content.encode("utf-8"))} 字节，'
                    f'内容摘要 {arguments.get("content_hash", "")[:12]}）')
        if tool_name == 'confirm_create_test_plan':
            return _preview_create_test_plan(arguments, user)
        if tool_name == 'confirm_update_test_plan':
            return _preview_update_test_plan(arguments, user)
        if tool_name == 'confirm_create_review':
            return _preview_create_review(arguments, user)
        if tool_name == 'confirm_update_review':
            return _preview_update_review(arguments, user)
        if tool_name == 'confirm_add_review_comment':
            return _preview_add_review_comment(arguments, user)
        if tool_name == 'confirm_update_review_comment':
            return _preview_update_review_comment(arguments, user)
    except Exception as exc:  # noqa: BLE001 - 预览失败不阻断流程
        logger.warning('构造 preview 失败 tool=%s: %s', tool_name, exc)
    return f'即将执行危险操作 {tool_name}'


ACTION_REGISTRY = {
    'confirm_run_api_suite': run_api_suite_action,
    'confirm_run_api_scenario': run_api_scenario_action,
    'confirm_run_ui_case': run_ui_case_action,
    'confirm_run_ui_suite': run_ui_suite_action,
    'confirm_run_perf_scene': run_perf_scene_action,
    'confirm_create_testcase': create_testcase_action,
    'confirm_create_ui_case': create_ui_case_action,
    'confirm_create_api_scenario_case': create_api_scenario_case_action,
    'confirm_create_ui_element': create_ui_element_action,
    'confirm_create_api_request': create_api_request_action,
    'confirm_update_testcase': update_testcase_action,
    'confirm_update_ui_case': update_ui_case_action,
    'confirm_update_api_scenario_case': update_api_scenario_case_action,
    'confirm_update_ui_element': update_ui_element_action,
    'confirm_import_recorded_script': import_recorded_script_action,
    'confirm_create_test_plan': create_test_plan_action,
    'confirm_update_test_plan': update_test_plan_action,
    'confirm_create_review': create_review_action,
    'confirm_update_review': update_review_action,
    'confirm_add_review_comment': add_review_comment_action,
    'confirm_update_review_comment': update_review_comment_action,
}
