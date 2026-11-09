from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.http import HttpResponse, FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from django.db import models, transaction
from django.utils import timezone
import logging
import json
import re
import random
import time
import shlex
from urllib.parse import urlparse
from urllib.parse import unquote

from .models import (
    UiProject, StorageState, LocatorStrategy, Element, TestScript, TestSuite,
    TestSuiteScript, TestExecution, Screenshot,
    ElementGroup, PageObject, PageObjectElement, ScriptStep, ScriptElementUsage,
    TestCase, TestCaseFolder, TestCaseStep, TestCaseExecution, OperationRecord,
    UiScheduledTask, UiNotificationLog, UiTaskNotificationSetting,
    AICase, AIExecutionRecord
)
from .serializers import (
    UiProjectSerializer, UiProjectCreateSerializer, UiProjectUpdateSerializer, StorageStateSerializer,
    LocatorStrategySerializer,
    ElementSerializer, ElementEnhancedSerializer, ElementCompactSerializer,
    resolve_page_group,
    TestScriptSerializer, TestScriptCreateSerializer, TestScriptUpdateSerializer,
    TestSuiteSerializer, TestSuiteCreateSerializer, TestSuiteUpdateSerializer, TestSuiteWithScriptsSerializer,
    TestSuiteScriptSerializer, TestSuiteTestCaseSerializer,
    TestExecutionSerializer, TestExecutionCreateSerializer,
    ScreenshotSerializer,
    ElementGroupSerializer, ElementGroupCreateSerializer,
    PageObjectSerializer, PageObjectCreateSerializer, PageObjectElementSerializer,
    ScriptStepSerializer, ScriptElementUsageSerializer,
    ScriptAnalysisSerializer, ElementValidationSerializer, CodeGenerationSerializer,
    TestCaseSerializer, TestCaseFolderSerializer, TestCaseStepSerializer, TestCaseExecutionSerializer, TestCaseRunSerializer,
    OperationRecordSerializer,
    UiScheduledTaskSerializer, UiNotificationLogSerializer, UiTaskNotificationSettingSerializer,
    AICaseSerializer, AIExecutionRecordSerializer,
    validate_test_case_step_data,
)
from .operation_logger import log_operation
from apps.projects.services import ensure_workspace_project_for_module, soft_delete_module_project, sync_project_members_from_module, sync_workspace_project_from_module
from .script_security import MAX_SCRIPT_BYTES, ScriptSecurityError, analyze_script
from .recording_import import import_recorded_script
from .recording_sync import UnsupportedRecordingCode, generate_code, parse_recording
from .script_runner import start_script_execution, stop_script_execution

logger = logging.getLogger(__name__)
User = get_user_model()


def _request_bool(value, default=False):
    """Parse JSON and multipart form boolean values consistently."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


LOCATOR_STRATEGY_NAMES = {
    'css': 'CSS', 'css selector': 'CSS', 'xpath': 'XPath', 'id': 'ID',
    'name': 'name', 'class': 'class', 'tag': 'tag', 'text': 'text',
    'placeholder': 'placeholder', 'role': 'role', 'label': 'label',
    'title': 'title', 'test-id': 'test-id', 'alt-text': 'alt-text',
}


def _canonical_locator_strategy(value):
    key = str(value or 'css').strip().lower()
    return LOCATOR_STRATEGY_NAMES.get(key, key)


def _canonical_storage_state_params(value):
    """复用手动用例的登录态动作 JSON 结构，避免同步编辑保存出多套格式。"""
    params = dict(value or {})
    operation = str(params.get('operation') or 'save').strip()
    if operation == 'save':
        return {
            'operation': 'save',
            'mode': params.get('mode') or 'save_as',
            'name': str(params.get('name') or '').strip(),
            'storage_state_id': params.get('storage_state_id') or None,
        }
    if operation == 'switch':
        return {
            'operation': 'switch',
            'storage_state_id': params.get('storage_state_id') or None,
        }
    if operation == 'clear':
        return {'operation': 'clear'}
    # 让现有校验在未知操作时返回明确错误，同时保留原始参数便于排查。
    return params


def _page_name_from_url(value):
    """从起始 URL 的最后一路径推断元素所属页面名称。"""
    parsed = urlparse(str(value or ''))
    segments = [segment for segment in parsed.path.split('/') if segment]
    if not segments:
        return ''
    name = re.sub(r'[-_]+', ' ', unquote(segments[-1])).strip()
    return name[:200]


def _element_metadata(action, locator):
    """根据动作和定位器语义推断元素类型及可辨识名称。"""
    locator = locator or {}
    options = locator.get('options') or {}
    strategy = str(locator.get('strategy') or '').lower()
    value = str(locator.get('value') or '')
    semantic_name = str(options.get('name') or options.get('label') or options.get('placeholder') or '')
    if not semantic_name and strategy in {'label', 'placeholder', 'text', 'test-id'}:
        semantic_name = value
    role = value.lower() if strategy == 'role' else ''
    if action == 'fill' or role in {'textbox', 'searchbox'} or strategy in {'label', 'placeholder'}:
        element_type, suffix = 'INPUT', '输入框'
    elif role == 'link':
        element_type, suffix = 'LINK', '链接'
    elif role in {'combobox', 'listbox'} or action == 'select':
        element_type, suffix = 'DROPDOWN', '下拉框'
    elif role == 'checkbox' or action in {'check', 'uncheck'}:
        element_type, suffix = 'CHECKBOX', '复选框'
    elif action in {'getText', 'assert'} or strategy in {'text', 'test-id'}:
        element_type, suffix = 'TEXT', '文本'
    else:
        element_type, suffix = 'BUTTON', '按钮'
    if semantic_name:
        name = f'{semantic_name}{suffix}'
    elif role:
        name = f'{value}{suffix}'
    else:
        short_value = value.strip()[:64]
        name = f'{strategy or "元素"}:{short_value}' if short_value else '录制元素'
    return element_type, name[:200]


def _resolve_element(project, user, action, locator, page_url, strategies):
    """按项目、定位策略和值复用元素；不存在时自动创建元素库记录。"""
    if not locator or not locator.get('value'):
        return None
    strategy_name = _canonical_locator_strategy(locator.get('strategy'))
    strategy = strategies.get(strategy_name.lower())
    if strategy is None:
        strategy = LocatorStrategy.objects.filter(name__iexact=strategy_name).first()
        if strategy is None:
            strategy = LocatorStrategy.objects.create(
                name=strategy_name,
                description='Playwright定位策略'
            )
        strategies[strategy_name.lower()] = strategy

    locator_value = str(locator['value'])[:500]
    locator_options = locator.get('options') or {}
    element = Element.objects.filter(
        project=project,
        locator_strategy=strategy,
        locator_value=locator_value,
        locator_options=locator_options,
    ).first()
    if element:
        return element

    page_name = _page_name_from_url(page_url)
    group = resolve_page_group(project, page_name)
    if group:
        page_name = group.name
    element_type, element_name = _element_metadata(action, locator)
    return Element.objects.create(
        project=project,
        group=group,
        page=page_name,
        name=element_name,
        element_type=element_type,
        locator_strategy=strategy,
        locator_value=locator_value,
        locator_options=locator_options,
        created_by=user,
    )


def _find_matching_element(project, locator):
    """反解析代码时查找完全匹配的已有元素，避免重复建库。"""
    if not locator or not locator.get('value'):
        return None
    strategy_name = _canonical_locator_strategy(locator.get('strategy'))
    strategy = LocatorStrategy.objects.filter(name__iexact=strategy_name).first()
    if strategy is None:
        return None
    return Element.objects.filter(
        project=project,
        locator_strategy=strategy,
        locator_value=str(locator['value'])[:500],
        locator_options=locator.get('options') or {},
    ).first()


def extract_step_info(s, step_index):
    """提取步骤信息的辅助函数，确保返回可读的步骤描述"""
    step_info = {'step': step_index}

    # 尝试多种方式提取可读信息
    if hasattr(s, 'action'):
        # 如果有action属性
        action_data = s.action
        if isinstance(action_data, str):
            step_info['action'] = action_data
        elif hasattr(action_data, '__dict__'):
            # 如果是对象，提取关键属性
            attrs = {}
            for key in ['type', 'description', 'goal', 'coordinate', 'text', 'output', 'result']:
                if hasattr(action_data, key):
                    value = getattr(action_data, key)
                    if isinstance(value, str):
                        attrs[key] = value
                    elif callable(value):
                        attrs[key] = getattr(value, '__name__', str(value))
                    else:
                        attrs[key] = str(value)
            if attrs:
                step_info['action'] = attrs
        else:
            step_info['action'] = str(action_data)
    elif hasattr(s, 'model_output'):
        # 如果有model_output属性
        output_data = s.model_output
        if isinstance(output_data, str):
            step_info['action'] = output_data
        elif hasattr(output_data, '__dict__'):
            # 提取model_output的关键信息
            attrs = {'type': 'model_output'}
            for key in ['action', 'description', 'goal', 'coordinate', 'text']:
                if hasattr(output_data, key):
                    value = getattr(output_data, key)
                    attrs[key] = str(value) if value else None
            step_info['action'] = attrs
        else:
            step_info['action'] = str(output_data)
    elif hasattr(s, '__dict__'):
        # 通用的对象提取
        attrs = {}
        for key in dir(s):
            if not key.startswith('_'):
                try:
                    value = getattr(s, key)
                    if not callable(value):
                        attrs[key] = str(value)
                except:
                    pass
        if attrs:
            step_info['action'] = attrs
    else:
        # 最后回退，但检查是否是函数对象
        if callable(s):
            step_info['action'] = f"<Action: {getattr(s, '__name__', 'unknown action')}>"
        else:
            step_info['action'] = str(s)

    return step_info


from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 1000


class UiProjectViewSet(viewsets.ModelViewSet):
    queryset = UiProject.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'owner', 'members']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'create':
            return UiProjectCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return UiProjectUpdateSerializer
        return UiProjectSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的项目
        user = self.request.user
        return UiProject.objects.filter(is_deleted=False).filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()

    def perform_create(self, serializer):
        # 创建项目时，当前用户自动成为负责人
        instance = serializer.save(owner=self.request.user)
        ensure_workspace_project_for_module(instance, 'ui', self.request.user)
        # 记录操作
        log_operation('create', 'project', instance.id, instance.name, self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()
        sync_project_members_from_module(instance)
        sync_workspace_project_from_module(instance, 'ui')
        # 记录操作
        log_operation('edit', 'project', instance.id, instance.name, self.request.user)

    def perform_destroy(self, instance):
        # 记录操作（在删除前记录）
        log_operation('delete', 'project', instance.id, instance.name, self.request.user)
        soft_delete_module_project(instance, self.request.user)


class StorageStateViewSet(viewsets.ModelViewSet):
    """提供登录态的项目权限过滤、文件管理和 JSON 内容编辑接口。"""
    queryset = StorageState.objects.all()
    serializer_class = StorageStateSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['project', 'browser']
    search_fields = ['name', 'source_url']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        projects = UiProject.objects.filter(models.Q(owner=user) | models.Q(members=user)).distinct()
        return StorageState.objects.filter(project__in=projects).select_related('project', 'created_by')

    def _project(self, project_id):
        return UiProject.objects.filter(
            pk=project_id,
        ).filter(models.Q(owner=self.request.user) | models.Q(members=self.request.user)).distinct().first()

    def perform_create(self, serializer):
        project_id = self.request.data.get('project') or self.request.data.get('project_id')
        project = self._project(project_id)
        if not project:
            raise ValidationError({'project': '项目不存在或无权访问'})
        instance = serializer.save(project=project, created_by=self.request.user)
        if instance.file and not instance.file_size:
            instance.file_size = instance.file.size
            instance.save(update_fields=['file_size'])
        log_operation('create', 'storage_state', instance.id, instance.name, self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()
        if instance.file:
            instance.file_size = instance.file.size
            instance.save(update_fields=['file_size'])
        log_operation('edit', 'storage_state', instance.id, instance.name, self.request.user)

    def perform_destroy(self, instance):
        name = instance.name
        if instance.file:
            instance.file.delete(save=False)
        log_operation('delete', 'storage_state', instance.id, name, self.request.user)
        instance.delete()

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        # 下载前复用 get_object，确保资源仍属于当前用户可访问的项目。
        instance = self.get_object()
        return FileResponse(instance.file.open('rb'), as_attachment=True, filename=f'{instance.name}.json')

    @action(detail=True, methods=['put'], parser_classes=[JSONParser])
    def content(self, request, pk=None):
        # 手工编辑只接受 Playwright storage_state 的标准 cookies/origins 结构。
        instance = self.get_object()
        payload = request.data.get('content', request.data)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValidationError({'content': '登录态 JSON 格式无效'}) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get('cookies', []), list) or not isinstance(payload.get('origins', []), list):
            raise ValidationError({'content': '登录态 JSON 必须包含 cookies 和 origins 数组'})
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        instance.write_content(raw)
        return Response(self.get_serializer(instance).data)


class LocatorStrategyViewSet(viewsets.ModelViewSet):
    queryset = LocatorStrategy.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = LocatorStrategySerializer
    ordering = ['id']


class ElementViewSet(viewsets.ModelViewSet):
    queryset = Element.objects.all()
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['project', 'locator_strategy', 'element_type', 'validation_status', 'group']
    search_fields = ['name', 'description', 'page', 'component_name']

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve'] and self.request.query_params.get('compact') == '1':
            return ElementCompactSerializer
        if self.action in ['list', 'retrieve']:
            return ElementEnhancedSerializer
        return ElementSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的项目的元素
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        queryset = Element.objects.filter(project__in=accessible_projects).select_related(
            'project', 'group', 'locator_strategy', 'created_by', 'parent_element'
        ).annotate(
            case_step_count=models.Count('testcasestep')
        ).order_by('page', 'order', 'name')
        if self.request.query_params.get('compact') != '1':
            queryset = queryset.prefetch_related('script_usages__script')
        return queryset

    def filter_queryset(self, queryset):
        # 先应用默认的过滤器
        queryset = super().filter_queryset(queryset)

        # 处理页面筛选（使用page_name参数避免与分页page冲突）
        page_name = self.request.query_params.get('page_name', None)
        if page_name:
            queryset = queryset.filter(page=page_name)

        return queryset

    def perform_create(self, serializer):
        # 创建元素时自动设置创建人
        instance = serializer.save(created_by=self.request.user)
        # 记录操作
        log_operation('create', 'element', instance.id, instance.name, self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()
        # 记录操作
        log_operation('edit', 'element', instance.id, instance.name, self.request.user)

    def perform_destroy(self, instance):
        # 记录操作（在删除前记录）
        log_operation('delete', 'element', instance.id, instance.name, self.request.user)
        instance.delete()

    @action(detail=True, methods=['post'])
    def validate_locator(self, request, pk=None):
        """验证元素定位器有效性"""
        element = self.get_object()

        # 这里可以集成实际的浏览器验证逻辑
        # 现在只是模拟验证
        validation_result = self._perform_element_validation(element)

        element.validation_status = 'VALID' if validation_result['is_valid'] else 'INVALID'
        element.validation_message = validation_result['validation_message']
        element.last_validated = timezone.now()
        element.save()

        serializer = ElementValidationSerializer(validation_result)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def usages(self, request, pk=None):
        """获取元素在脚本中的使用情况"""
        element = self.get_object()
        usages = ScriptElementUsage.objects.filter(element=element).select_related('script')
        serializer = ScriptElementUsageSerializer(usages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """获取元素树形结构"""
        project_id = request.query_params.get('project')
        if not project_id:
            return Response({'error': '需要指定项目ID'}, status=status.HTTP_400_BAD_REQUEST)

        elements = self.get_queryset().filter(project_id=project_id)
        tree_data = self._build_element_tree(elements)
        return Response(tree_data)

    @action(detail=True, methods=['post'])
    def add_backup_locator(self, request, pk=None):
        """添加备用定位器"""
        element = self.get_object()
        strategy = request.data.get('strategy')
        value = request.data.get('value')

        if not strategy or not value:
            return Response({'error': '策略和值都是必需的'}, status=status.HTTP_400_BAD_REQUEST)

        backup_locators = element.backup_locators or []
        backup_locators.append({'strategy': strategy, 'value': value})
        element.backup_locators = backup_locators
        element.save()

        return Response({'message': '备用定位器添加成功'})

    @action(detail=True, methods=['post'])
    def generate_suggestions(self, request, pk=None):
        """生成元素使用建议"""
        element = self.get_object()
        suggestions = self._generate_element_suggestions(element)
        return Response({'suggestions': suggestions})

    def _perform_element_validation(self, element):
        """执行元素验证（模拟实现）"""
        try:
            # 这里可以集成实际的浏览器自动化工具进行验证
            # 现在只是简单的语法检查
            is_valid = True
            message = "定位器验证通过"
            suggestions = []

            # 简单的语法检查
            if element.locator_strategy.name == 'css':
                if not element.locator_value.strip():
                    is_valid = False
                    message = "CSS选择器不能为空"
            elif element.locator_strategy.name == 'xpath':
                if not element.locator_value.strip():
                    is_valid = False
                    message = "XPath表达式不能为空"

            return {
                'is_valid': is_valid,
                'validation_message': message,
                'suggestions': suggestions
            }
        except Exception as e:
            return {
                'is_valid': False,
                'validation_message': f'验证过程中出现错误: {str(e)}',
                'suggestions': []
            }

    def _build_element_tree(self, elements):
        """构建元素树形结构 - 返回元素列表而不是页面分组，因为前端会自己处理页面关联"""
        element_data_list = []
        for element in elements:
            element_data = {
                'id': element.id,
                'name': element.name,
                'type': 'element',
                'element_type': element.element_type,
                'locator_strategy': element.locator_strategy.name if element.locator_strategy else None,
                'locator_value': element.locator_value,
                'locator_options': element.locator_options or {},
                'validation_status': element.validation_status,
                'usage_count': element.case_step_count,
                'order': element.order,
                'group_id': element.group_id,  # 用于前端关联到页面
                'page': element.page,  # 保留向后兼容
                'children': []
            }
            element_data_list.append(element_data)

        return element_data_list

    def _generate_element_suggestions(self, element):
        """生成元素使用建议"""
        suggestions = []

        # 基于元素类型生成建议
        if element.element_type == 'INPUT':
            suggestions.append("建议为输入框元素添加清空和输入验证操作")
        elif element.element_type == 'BUTTON':
            suggestions.append("建议验证按钮点击后的页面跳转或状态变化")
        elif element.element_type == 'DROPDOWN':
            suggestions.append("建议测试下拉框的所有选项")

        # 基于使用频率生成建议
        usage_count = element.case_step_count
        if usage_count == 0:
            suggestions.append("此元素尚未被任何正式用例步骤引用，考虑是否需要删除")
        elif usage_count > 10:
            suggestions.append("此元素被正式用例步骤引用较多，建议添加到页面对象中以提高复用性")

        return suggestions


class ElementGroupViewSet(viewsets.ModelViewSet):
    queryset = ElementGroup.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['project', 'parent_group']
    search_fields = ['name', 'description']

    def get_serializer_class(self):
        if self.action == 'create':
            return ElementGroupCreateSerializer
        return ElementGroupSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的项目的元素分组
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return ElementGroup.objects.filter(project__in=accessible_projects).select_related('project',
                                                                                           'parent_group').order_by(
            'order', 'name')

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """获取分组树形结构"""
        project_id = request.query_params.get('project')
        if not project_id:
            return Response({'error': '需要指定项目ID'}, status=status.HTTP_400_BAD_REQUEST)

        groups = self.get_queryset().filter(project_id=project_id, parent_group__isnull=True)
        serializer = ElementGroupSerializer(groups, many=True)
        return Response(serializer.data)


class PageObjectViewSet(viewsets.ModelViewSet):
    queryset = PageObject.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['project']
    search_fields = ['name', 'class_name', 'description']

    def get_serializer_class(self):
        if self.action == 'create':
            return PageObjectCreateSerializer
        return PageObjectSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的项目的页面对象
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return PageObject.objects.filter(project__in=accessible_projects).select_related(
            'project', 'created_by'
        ).prefetch_related('page_object_elements__element').order_by('-created_at')

    @action(detail=True, methods=['post'])
    def generate_code(self, request, pk=None):
        """生成页面对象代码"""
        page_object = self.get_object()
        serializer = CodeGenerationSerializer(data=request.data)

        if serializer.is_valid():
            language = serializer.validated_data['language']
            framework = serializer.validated_data['framework']
            include_comments = serializer.validated_data['include_comments']

            try:
                generated_code = page_object.generate_code(language)

                # 保存生成的代码模板
                page_object.template_code = generated_code
                page_object.save()

                return Response({
                    'code': generated_code,
                    'language': language,
                    'framework': framework
                })
            except Exception as e:
                return Response({
                    'error': f'代码生成失败: {str(e)}'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def add_element(self, request, pk=None):
        """向页面对象添加元素"""
        page_object = self.get_object()
        serializer = PageObjectElementSerializer(data=request.data)

        if serializer.is_valid():
            serializer.save(page_object=page_object)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def elements(self, request, pk=None):
        """获取页面对象的所有元素"""
        page_object = self.get_object()
        po_elements = page_object.page_object_elements.select_related('element').all()
        serializer = PageObjectElementSerializer(po_elements, many=True)
        return Response(serializer.data)


class PageObjectElementViewSet(viewsets.ModelViewSet):
    queryset = PageObjectElement.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = PageObjectElementSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的页面对象元素
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return PageObjectElement.objects.filter(
            page_object__project__in=accessible_projects
        ).select_related('page_object', 'element').order_by('id')


class ScriptStepViewSet(viewsets.ModelViewSet):
    queryset = ScriptStep.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = ScriptStepSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['script', 'action_type', 'target_element', 'page_object']

    def get_queryset(self):
        # 只显示用户有权限访问的脚本步骤
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return ScriptStep.objects.filter(
            script__project__in=accessible_projects
        ).select_related('script', 'target_element', 'page_object').order_by('step_order')

    @action(detail=False, methods=['post'])
    def batch_create(self, request):
        """批量创建脚本步骤"""
        steps_data = request.data.get('steps', [])
        created_steps = []

        for step_data in steps_data:
            serializer = ScriptStepSerializer(data=step_data)
            if serializer.is_valid():
                step = serializer.save()
                created_steps.append(step)
            else:
                return Response({
                    'error': f'步骤创建失败: {serializer.errors}'
                }, status=status.HTTP_400_BAD_REQUEST)

        response_serializer = ScriptStepSerializer(created_steps, many=True)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class ScriptElementUsageViewSet(viewsets.ModelViewSet):
    queryset = ScriptElementUsage.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = ScriptElementUsageSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['script', 'element', 'usage_type']

    def get_queryset(self):
        # 只显示用户有权限访问的脚本元素使用记录
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return ScriptElementUsage.objects.filter(
            script__project__in=accessible_projects
        ).select_related('script', 'element').order_by('script', 'line_number')

    @action(detail=False, methods=['post'])
    def analyze_script(self, request):
        """分析脚本中的元素使用情况"""
        script_id = request.data.get('script_id')
        if not script_id:
            return Response({'error': '需要指定脚本ID'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            script = TestScript.objects.get(id=script_id)
            analysis_result = self._analyze_script_elements(script)

            serializer = ScriptAnalysisSerializer(analysis_result)
            return Response(serializer.data)
        except TestScript.DoesNotExist:
            return Response({'error': '脚本不存在'}, status=status.HTTP_404_NOT_FOUND)

    def _analyze_script_elements(self, script):
        """分析脚本中的元素使用"""
        # 解析脚本内容，查找元素使用情况
        content = script.content
        usages = []
        missing_elements = []
        recommendations = []

        # 简单的元素使用分析（实际实现会更复杂）
        if script.script_type == 'CODE':
            # 分析代码中的定位器使用
            locator_patterns = [
                r'locator\(["\']([^"\']+)["\']\)',
                r'findElement\(["\']([^"\']+)["\']\)',
                r'css\(["\']([^"\']+)["\']\)',
                r'xpath\(["\']([^"\']+)["\']\)'
            ]

            for pattern in locator_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    # 查找对应的元素
                    try:
                        element = Element.objects.get(
                            project=script.project,
                            locator_value=match
                        )
                        usage, created = ScriptElementUsage.objects.get_or_create(
                            script=script,
                            element=element,
                            defaults={
                                'usage_type': 'CLICK',  # 默认类型
                                'line_number': 1,  # 需要实际解析
                                'frequency': 1
                            }
                        )
                        if not created:
                            usage.frequency += 1
                            usage.save()

                        usages.append(usage)
                    except Element.DoesNotExist:
                        missing_elements.append(match)

        # 生成建议
        if missing_elements:
            recommendations.append(f"发现 {len(missing_elements)} 个未定义的元素定位器")

        if len(usages) > 20:
            recommendations.append("脚本复杂度较高，建议拆分为多个小脚本")

        complexity_score = min(100, len(usages) * 5)

        return {
            'element_usages': usages,
            'missing_elements': missing_elements,
            'recommendations': recommendations,
            'complexity_score': complexity_score
        }


class TestScriptViewSet(viewsets.ModelViewSet):
    queryset = TestScript.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['project', 'script_type']
    search_fields = ['name', 'description']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'create':
            return TestScriptCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return TestScriptUpdateSerializer
        return TestScriptSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的项目的测试脚本
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return TestScript.objects.filter(project__in=accessible_projects)

    @action(detail=True, methods=['post'], url_path='parse-steps')
    def parse_steps(self, request, pk=None):
        """解析编辑器代码，并把定位器匹配到当前项目的元素库。"""
        script = self.get_object()
        content = request.data.get('content', script.content)
        try:
            analysis = analyze_script(content)
            steps = parse_recording(content)
        except (ScriptSecurityError, UnsupportedRecordingCode) as exc:
            return Response({'error': str(exc), 'line': getattr(exc, 'line', None)}, status=status.HTTP_400_BAD_REQUEST)
        start_url = analysis['start_url']
        for step in steps:
            locator = (step.get('action_params') or {}).get('locator')
            element = _find_matching_element(script.project, locator)
            step['element_id'] = element.id if element else ''
            if element:
                step['target_element'] = {
                    'id': element.id,
                    'name': element.name,
                    'page': element.page,
                    'locator_value': element.locator_value,
                    'locator_options': element.locator_options,
                    'locator_strategy': {'name': element.locator_strategy.name},
                }
        return Response({'steps': steps, 'start_url': start_url})

    @action(detail=True, methods=['put'], url_path='editor')
    def save_editor(self, request, pk=None):
        """事务性保存脚本和步骤，同时创建或复用元素库记录。"""
        script = self.get_object()
        steps = request.data.get('steps', [])
        content = request.data.get('content', '')
        if not isinstance(steps, list) or not isinstance(content, str):
            return Response({'error': '脚本或步骤数据格式无效'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            analyze_script(content)
            with transaction.atomic():
                script.name = str(request.data.get('name') or script.name).strip()[:200]
                script.content = content
                script.start_url = str(request.data.get('start_url') or '')[:500]
                script.save(update_fields=['name', 'content', 'start_url', 'updated_at'])
                script.steps.all().delete()
                strategies = {item.name.lower(): item for item in LocatorStrategy.objects.all()}
                page_url = script.start_url or request.data.get('start_url') or ''
                if not page_url:
                    page_url = next((str(step.get('input_value') or '') for step in steps
                                     if step.get('action_type') == 'navigate' and step.get('input_value')), '')
                created_steps = []
                for index, raw in enumerate(steps, 1):
                    data = dict(raw)
                    params = data.get('action_params') or {}
                    locator = params.get('locator') or {
                        'strategy': data.get('locator_strategy'),
                        'value': data.get('locator_value'),
                        'options': {'name': data.get('locator_name')} if data.get('locator_name') else {},
                    }
                    if locator.get('strategy') and locator.get('value'):
                        params['locator'] = locator
                    element = None
                    element_id = data.get('element_id') or data.get('target_element')
                    if element_id:
                        element = Element.objects.filter(pk=element_id, project_id=script.project_id).first()
                        if not element:
                            raise ValidationError({'steps': [f'步骤{index}的元素不属于当前项目']})
                        if locator.get('value') and (
                            element.locator_value != str(locator['value']) or
                            element.locator_strategy.name.lower() != str(locator.get('strategy') or '').lower() or
                            (element.locator_options or {}) != (locator.get('options') or {})
                        ):
                            element = None
                    if element is None and locator.get('value'):
                        element = _resolve_element(
                            script.project, request.user, data.get('action_type'), locator,
                            page_url, strategies
                        )
                    action = str(data.get('action_type') or '').strip()
                    if action == 'storageState':
                        params = _canonical_storage_state_params(params)
                    stored_action = {
                        'click': 'CLICK', 'fill': 'INPUT', 'press': 'PRESS', 'select': 'SELECT',
                        'check': 'CHECK', 'uncheck': 'UNCHECK', 'getText': 'GETTEXT',
                        'waitFor': 'WAITFOR', 'assert': 'VERIFY', 'wait': 'WAIT',
                        'hover': 'HOVER', 'scroll': 'SCROLL', 'navigate': 'NAVIGATE',
                        'screenshot': 'SCREENSHOT', 'switchTab': 'SWITCH_TAB', 'storageState': 'STORAGE_STATE',
                    }.get(action, action.upper() or 'CUSTOM')
                    created_steps.append(ScriptStep(
                        script=script,
                        step_order=index,
                        action_type=stored_action,
                        target_element=element,
                        action_params=params,
                        input_value=str(data.get('input_value') or ''),
                        wait_time=int(data.get('wait_time') or 1000),
                        assert_type=str(data.get('assert_type') or ''),
                        assert_value=str(data.get('assert_value') or ''),
                        description=str(data.get('description') or f'步骤{index}')[:500],
                        raw_code=str(data.get('raw_code') or ''),
                        source_line_start=data.get('source_line_start'),
                        source_line_end=data.get('source_line_end'),
                    ))
                ScriptStep.objects.bulk_create(created_steps)
                script.analysis = {**(script.analysis or {}), 'steps_synced': True}
                script.save(update_fields=['analysis', 'updated_at'])
            return Response(TestScriptSerializer(script, context={'request': request}).data)
        except ScriptSecurityError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='generate-code')
    def generate_editor_code(self, request, pk=None):
        """根据结构化步骤生成编辑器右侧代码。"""
        self.get_object()
        return Response({'content': generate_code(request.data.get('steps', []), request.data.get('start_url', ''))})

    @action(detail=True, methods=['post'], url_path='to-test-case')
    def to_test_case(self, request, pk=None):
        """把已同步的脚本步骤复制为可执行 UI 测试用例。"""
        script = self.get_object()
        steps = list(script.steps.select_related('target_element').order_by('step_order'))
        if not steps:
            return Response({'error': '请先在编辑器中完成步骤同步并保存'}, status=status.HTTP_400_BAD_REQUEST)
        action_map = {'CLICK': 'click', 'INPUT': 'fill', 'PRESS': 'press', 'HOVER': 'hover', 'WAIT': 'wait', 'WAITFOR': 'waitFor', 'NAVIGATE': 'navigate', 'SCREENSHOT': 'screenshot', 'VERIFY': 'assert', 'GETTEXT': 'getText', 'SELECT': 'select', 'CHECK': 'check', 'UNCHECK': 'uncheck', 'SCROLL': 'scroll', 'SWITCH_TAB': 'switchTab', 'STORAGE_STATE': 'storageState', 'CUSTOM': None}
        unsupported = [step.step_order for step in steps if action_map.get(step.action_type) is None]
        if unsupported:
            return Response({'error': f'步骤 {", ".join(map(str, unsupported))} 尚不能转换为可执行测试用例'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            test_case = TestCase.objects.create(
                project=script.project,
                name=str(request.data.get('name') or script.name.removesuffix('.py'))[:200],
                description=str(request.data.get('description') or script.description),
                priority=request.data.get('priority', 'medium'),
                created_by=request.user,
            )
            case_steps = []
            conversion_steps = steps
            start_url = str(request.data.get('start_url') or script.start_url or '').strip()
            if start_url and not any(step.action_type == 'NAVIGATE' for step in steps):
                case_steps.append(TestCaseStep(
                    test_case=test_case,
                    step_number=1,
                    action_type='navigate',
                    input_value=start_url,
                    description='打开起始URL',
                ))
            for index, step in enumerate(conversion_steps, len(case_steps) + 1):
                action = action_map.get(step.action_type)
                if action is None:
                    continue
                action_params = step.action_params or {}
                if action == 'storageState':
                    action_params = _canonical_storage_state_params(action_params)
                case_steps.append(TestCaseStep(
                    test_case=test_case,
                    step_number=index,
                    action_type=action,
                    element=step.target_element,
                    # 登录态动作沿用手动用例的 JSON 参数，不能在转换时丢失。
                    action_params=action_params,
                    input_value=step.input_value or action_params.get('select_value', ''),
                    wait_time=step.wait_time,
                    assert_type=step.assert_type,
                    assert_value=step.assert_value,
                    description=step.description,
                ))
            TestCaseStep.objects.bulk_create(case_steps)
        log_operation('create', 'test_case', test_case.id, test_case.name, request.user)
        return Response(TestCaseSerializer(test_case, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='preflight')
    def preflight(self, request):
        """Run the same AST safety and replay-risk checks before execution."""
        content = request.data.get('content')
        if not isinstance(content, str) or not content.strip():
            return Response({'error': '脚本内容不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            analysis = analyze_script(content)
        except ScriptSecurityError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'content_hash': analysis['content_hash'],
            'locator_count': analysis['locator_count'],
            'start_url': analysis['start_url'],
            'diagnostics': analysis['diagnostics'],
        })

    @action(detail=False, methods=['post'], url_path='recording-command')
    def recording_command(self, request):
        """Build the local Playwright codegen command used by the recording wizard."""
        start_url = str(request.data.get('start_url', '')).strip()
        browser = str(request.data.get('browser', 'chromium')).strip().lower()
        if urlparse(start_url).scheme not in {'http', 'https'} or not urlparse(start_url).netloc:
            return Response({'error': '请输入有效的http/https起始URL'}, status=status.HTTP_400_BAD_REQUEST)
        if browser != 'chromium':
            return Response({'error': '本期录制与回放仅支持Chromium'}, status=status.HTTP_400_BAD_REQUEST)

        output_name = str(request.data.get('output_name') or 'testhub_recorded.py').strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.py', output_name):
            output_name = 'testhub_recorded.py'
        target = 'python'
        install_command = f'python -m pip install playwright && python -m playwright install {browser}'
        command_parts = [
            'playwright', 'codegen', start_url,
            '--target=' + target,
            '--browser=' + browser,
            '--output=' + output_name,
            '--ignore-https-errors',
        ]
        if request.data.get('save_storage'):
            command_parts.extend(['--save-storage=storage_state.json'])
        return Response({
            'install_command': install_command,
            'record_command': ' '.join(shlex.quote(part) for part in command_parts),
            'output_name': output_name,
            'language': 'python',
            'framework': 'playwright',
        })

    @action(detail=False, methods=['post'], url_path='import', parser_classes=[JSONParser, FormParser, MultiPartParser])
    def import_script(self, request):
        """Validate and persist a Python Playwright recording."""
        project_id = request.data.get('project') or request.data.get('project_id')
        if not project_id:
            return Response({'error': '请选择项目'}, status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        project = UiProject.objects.filter(
            pk=project_id,
        ).filter(models.Q(owner=user) | models.Q(members=user)).distinct().first()
        if not project:
            return Response({'error': '项目不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)

        uploaded = request.FILES.get('file')
        if uploaded:
            if uploaded.size > MAX_SCRIPT_BYTES:
                return Response({'error': '脚本文件不能超过2MB'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                content = uploaded.read().decode('utf-8')
            except UnicodeDecodeError:
                return Response({'error': '脚本必须使用UTF-8编码'}, status=status.HTTP_400_BAD_REQUEST)
            default_name = uploaded.name.rsplit('/', 1)[-1]
        else:
            content = str(request.data.get('content') or '')
            default_name = 'testhub_recorded.py'
        try:
            script, analysis = import_recorded_script(
                project=project,
                user=user,
                content=content,
                name=request.data.get('name') or default_name,
                start_url=request.data.get('start_url'),
                browser=request.data.get('browser', 'chromium'),
                device=request.data.get('device', ''),
                save_storage=request.data.get('save_storage'),
            )
        except (UnicodeDecodeError, ScriptSecurityError) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = TestScriptSerializer(script, context={'request': request}).data
        payload['analysis'] = script.analysis
        return Response(payload, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='run')
    def run_recorded_script(self, request, pk=None):
        script = self.get_object()
        if script.language != 'python' or script.framework != 'playwright':
            return Response({'error': '本期只支持Python Playwright脚本'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            analysis = analyze_script(script.content)
            browser = str(request.data.get('browser') or script.recording_config.get('browser') or 'chromium').lower()
            if browser not in {'chromium', 'chrome'}:
                return Response({'error': '本期回放仅支持Chromium'}, status=status.HTTP_400_BAD_REQUEST)
            headless = _request_bool(request.data.get('headless'), True)
            # 记录回放时只允许使用当前脚本项目下的登录态。
            storage_state_id = request.data.get('storage_state_id')
            selected_state = None
            if storage_state_id:
                selected_state = StorageState.objects.filter(
                    pk=storage_state_id, project=script.project
                ).first()
                if not selected_state:
                    return Response({'error': '登录态不存在或不属于当前项目'}, status=status.HTTP_400_BAD_REQUEST)
            execution = TestExecution.objects.create(
                project=script.project,
                test_script=script,
                execution_type='recorded_script',
                status='PENDING',
                engine='playwright',
                browser='chromium',
                headless=headless,
                executed_by=request.user,
                run_config={
                    'base_url': str(request.data.get('base_url') or '').strip(),
                    'content_hash': analysis['content_hash'],
                    'script_source': 'saved_snapshot',
                    'storage_state_id': selected_state.id if selected_state else None,
                },
            )
            log_operation('run', 'script', script.id, script.name, request.user)
            start_script_execution(execution.id)
            return Response(TestExecutionSerializer(execution, context={'request': request}).data, status=status.HTTP_202_ACCEPTED)
        except Exception as exc:
            logger.exception('创建脚本回放失败')
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='run-unsaved')
    def run_unsaved_script(self, request):
        """Run editor content in an isolated temporary directory without saving it."""
        project_id = request.data.get('project') or request.data.get('project_id')
        project = UiProject.objects.filter(pk=project_id).filter(
            models.Q(owner=request.user) | models.Q(members=request.user)
        ).distinct().first()
        if not project:
            return Response({'error': '项目不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)

        content = request.data.get('content')
        if not isinstance(content, str) or not content.strip():
            return Response({'error': '脚本内容不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            analysis = analyze_script(content)
            browser = str(request.data.get('browser') or 'chromium').lower()
            if browser not in {'chromium', 'chrome'}:
                return Response({'error': '本期回放仅支持Chromium'}, status=status.HTTP_400_BAD_REQUEST)
            # 未保存脚本也沿用相同的项目边界校验，避免跨项目读取登录态。
            storage_state_id = request.data.get('storage_state_id')
            selected_state = None
            if storage_state_id:
                selected_state = StorageState.objects.filter(
                    pk=storage_state_id, project=project
                ).first()
                if not selected_state:
                    return Response({'error': '登录态不存在或不属于当前项目'}, status=status.HTTP_400_BAD_REQUEST)
            execution = TestExecution.objects.create(
                project=project,
                test_script=None,
                execution_type='recorded_script',
                status='PENDING',
                engine='playwright',
                browser='chromium',
                headless=_request_bool(request.data.get('headless'), True),
                executed_by=request.user,
                run_config={
                    'base_url': str(request.data.get('base_url') or '').strip(),
                    'start_url': str(request.data.get('start_url') or analysis.get('start_url') or '').strip(),
                    'content_hash': analysis['content_hash'],
                    'script_source': 'editor_snapshot',
                    'storage_state_id': selected_state.id if selected_state else None,
                },
            )
            start_script_execution(execution.id, analysis['normalized_content'])
            return Response(
                TestExecutionSerializer(execution, context={'request': request}).data,
                status=status.HTTP_202_ACCEPTED,
            )
        except (UnicodeDecodeError, ScriptSecurityError) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception('创建未保存脚本回放失败')
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='executions')
    def script_executions(self, request, pk=None):
        script = self.get_object()
        queryset = TestExecution.objects.filter(test_script=script).select_related('executed_by')
        page = self.paginate_queryset(queryset)
        serializer = TestExecutionSerializer(page or queryset, many=True, context={'request': request})
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class TestSuiteViewSet(viewsets.ModelViewSet):
    queryset = TestSuite.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['project']
    search_fields = ['name', 'description']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'create':
            return TestSuiteCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return TestSuiteUpdateSerializer
        elif self.action == 'retrieve':
            return TestSuiteWithScriptsSerializer
        return TestSuiteSerializer

    def get_queryset(self):
        # 只显示用户有权限访问的项目的测试套件
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return TestSuite.objects.filter(project__in=accessible_projects)

    def perform_create(self, serializer):
        instance = serializer.save()
        # 记录操作
        log_operation('create', 'suite', instance.id, instance.name, self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()
        # 记录操作
        log_operation('edit', 'suite', instance.id, instance.name, self.request.user)

    def perform_destroy(self, instance):
        # 记录操作（在删除前记录）
        log_operation('delete', 'suite', instance.id, instance.name, self.request.user)
        instance.delete()

    @action(detail=True, methods=['get'])
    def scripts(self, request, pk=None):
        """获取测试套件中的所有脚本"""
        test_suite = self.get_object()
        scripts = test_suite.suite_scripts.all()
        serializer = TestSuiteScriptSerializer(scripts, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def add_script(self, request, pk=None):
        """向测试套件添加脚本"""
        test_suite = self.get_object()
        data = request.data
        data['test_suite'] = pk
        serializer = TestSuiteScriptSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['delete'])
    def remove_script(self, request, pk=None, script_id=None):
        """从测试套件移除脚本"""
        test_suite = self.get_object()
        try:
            suite_script = TestSuiteScript.objects.get(test_suite=test_suite, id=script_id)
            suite_script.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except TestSuiteScript.DoesNotExist:
            return Response({'error': '脚本不存在于该测试套件中'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['get'])
    def test_cases(self, request, pk=None):
        """获取测试套件中的所有测试用例"""
        test_suite = self.get_object()
        test_cases = test_suite.suite_test_cases.all()
        serializer = TestSuiteTestCaseSerializer(test_cases, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def add_test_case(self, request, pk=None):
        """向测试套件添加测试用例"""
        test_suite = self.get_object()
        test_case_id = request.data.get('test_case_id')
        order = request.data.get('order', 0)

        try:
            from .models import TestSuiteTestCase
            suite_test_case = TestSuiteTestCase.objects.create(
                test_suite=test_suite,
                test_case_id=test_case_id,
                order=order
            )
            serializer = TestSuiteTestCaseSerializer(suite_test_case)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['delete'])
    def remove_test_case(self, request, pk=None):
        """从测试套件移除测试用例"""
        test_suite = self.get_object()
        test_case_id = request.data.get('test_case_id')

        try:
            from .models import TestSuiteTestCase
            suite_test_case = TestSuiteTestCase.objects.get(
                test_suite=test_suite,
                test_case_id=test_case_id
            )
            suite_test_case.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except TestSuiteTestCase.DoesNotExist:
            return Response({'error': '测试用例不存在于该测试套件中'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def update_test_case_order(self, request, pk=None):
        """更新测试套件中测试用例的顺序"""
        test_suite = self.get_object()
        test_case_orders = request.data.get('test_case_orders', [])

        try:
            from .models import TestSuiteTestCase
            for item in test_case_orders:
                TestSuiteTestCase.objects.filter(
                    test_suite=test_suite,
                    test_case_id=item['test_case_id']
                ).update(order=item['order'])

            return Response({'message': '顺序更新成功'}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def run_suite(self, request, pk=None):
        """执行测试套件"""
        test_suite = self.get_object()

        # 传统模式执行（Playwright/Selenium）
        # 检查是否包含测试用例
        test_case_count = test_suite.suite_test_cases.count()
        if test_case_count == 0:
            return Response({
                'error': '该测试套件未包含任何测试用例，无法执行'
            }, status=status.HTTP_400_BAD_REQUEST)

        engine = request.data.get('engine', 'playwright')
        browser = request.data.get('browser', 'chrome')
        headless = request.data.get('headless', False)
        storage_state_id = request.data.get('storage_state_id') if engine == 'playwright' else None
        if storage_state_id:
            initial_state = StorageState.objects.filter(
                pk=storage_state_id, project=test_suite.project
            ).first()
            if not initial_state:
                return Response({'error': '登录态不存在或不属于当前项目'}, status=status.HTTP_400_BAD_REQUEST)
            if not initial_state.file or not initial_state.file.storage.exists(initial_state.file.name):
                return Response({'error': '登录态文件不存在，请重新上传或保存该登录态'}, status=status.HTTP_400_BAD_REQUEST)

        if engine == 'selenium':
            from .selenium_engine import SeleniumTestEngine
            is_ready, error_msg = SeleniumTestEngine.check_execution_environment(browser)
            if not is_ready:
                return Response({
                    'error': error_msg,
                    'message': '浏览器驱动未就绪，请先安装后再执行测试套件'
                }, status=status.HTTP_400_BAD_REQUEST)
        elif engine == 'playwright':
            from .playwright_engine import PlaywrightTestEngine
            is_ready, error_msg = PlaywrightTestEngine.check_execution_environment_sync(browser)
            if not is_ready:
                return Response({
                    'error': error_msg,
                    'message': 'Playwright 浏览器未就绪，请先安装后再执行测试套件'
                }, status=status.HTTP_400_BAD_REQUEST)

        # 更新套件执行状态为运行中
        test_suite.execution_status = 'running'
        test_suite.save()

        # 记录运行操作
        log_operation('run', 'suite', test_suite.id, test_suite.name, request.user)

        # 在后台线程中执行测试
        import threading
        import traceback
        from .test_executor import TestExecutor

        def run_test():
            try:
                print(f"[测试套件] 开始执行: {test_suite.name} (ID: {test_suite.id})")
                print(f"[测试套件] 配置: engine={engine}, browser={browser}, headless={headless}")

                executor = TestExecutor(
                    test_suite=test_suite,
                    engine=engine,
                    browser=browser,
                    headless=headless,
                    executed_by=request.user,
                    storage_state_id=storage_state_id
                )
                executor.run()

                print(f"[测试套件] 执行完成: {test_suite.name}")
            except Exception as e:
                print(f"[测试套件] 执行异常: {test_suite.name}")
                print(f"[测试套件] 错误: {str(e)}")
                traceback.print_exc()

                # 更新套件状态为失败
                try:
                    test_suite.execution_status = 'failed'
                    test_suite.save()
                    print(f"[测试套件] 已更新状态为失败")
                except Exception as save_error:
                    print(f"[测试套件] 更新状态失败: {save_error}")

        # 启动后台线程执行测试
        thread = threading.Thread(target=run_test, daemon=False)
        thread.start()

        return Response({
            'message': '测试套件开始执行',
            'suite_id': test_suite.id,
            'test_case_count': test_case_count,
            'engine': engine,
            'browser': browser,
            'headless': headless
        }, status=status.HTTP_200_OK)


class TestExecutionViewSet(viewsets.ModelViewSet):
    queryset = TestExecution.objects.all()
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['project', 'test_suite', 'test_script', 'status', 'environment', 'executed_by']
    search_fields = ['error_message']
    ordering = ['-created_at']
    pagination_class = StandardPagination

    def get_queryset(self):
        # 只显示用户有权限访问的项目的测试执行记录
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return TestExecution.objects.filter(
            project__in=accessible_projects
        ).select_related('project', 'test_suite', 'test_script', 'executed_by')

    def get_serializer_class(self):
        if self.action == 'create':
            return TestExecutionCreateSerializer
        return TestExecutionSerializer

    def perform_destroy(self, instance):
        # 记录操作（删除测试报告）
        suite_name = instance.test_suite.name if instance.test_suite else f"执行记录#{instance.id}"
        log_operation('delete', 'report', instance.id, suite_name, self.request.user)
        instance.delete()

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        execution = self.get_object()
        return Response({
            'id': execution.id,
            'status': execution.status,
            'logs': execution.execution_logs or '',
            'error_message': execution.error_message or '',
            'duration': execution.duration,
        })

    @action(detail=True, methods=['post'], url_path='abort')
    def abort(self, request, pk=None):
        execution = self.get_object()
        if execution.status not in {'PENDING', 'RUNNING'}:
            return Response({'error': '当前执行已结束'}, status=status.HTTP_400_BAD_REQUEST)
        stop_script_execution(execution.id)
        execution.status = 'ABORTED'
        execution.finished_at = timezone.now()
        execution.error_message = '用户手动终止执行'
        execution.save(update_fields=['status', 'finished_at', 'error_message'])
        return Response(TestExecutionSerializer(execution, context={'request': request}).data)


class ScreenshotViewSet(viewsets.ModelViewSet):
    queryset = Screenshot.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = ScreenshotSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['execution']

    def get_queryset(self):
        # 只显示用户有权限访问的项目的截图
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        executions = TestExecution.objects.filter(project__in=accessible_projects)
        return Screenshot.objects.filter(execution__in=executions)


class TestCaseFolderViewSet(viewsets.ModelViewSet):
    """UI 测试用例文件夹接口，使用父子关系管理嵌套目录。"""
    serializer_class = TestCaseFolderSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        queryset = TestCaseFolder.objects.filter(
            project__in=UiProject.objects.filter(models.Q(owner=user) | models.Q(members=user))
        ).distinct()
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not UiProject.objects.filter(
            pk=project.pk,
        ).filter(models.Q(owner=self.request.user) | models.Q(members=self.request.user)).exists():
            raise ValidationError({'project': '无权访问该项目'})
        parent = serializer.validated_data.get('parent')
        if parent and parent.project_id != project.pk:
            raise ValidationError({'parent': '父文件夹必须属于当前项目'})
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        folder = self.get_object()
        if serializer.validated_data.get('project', folder.project) != folder.project:
            raise ValidationError({'project': '文件夹不能移动到其他项目'})
        parent = serializer.validated_data.get('parent', folder.parent)
        if parent and parent.project_id != folder.project_id:
            raise ValidationError({'parent': '父文件夹必须属于当前项目'})
        if parent and parent.pk == folder.pk:
            raise ValidationError({'parent': '文件夹不能设置自身为父目录'})
        if parent:
            descendant_ids = {folder.pk}
            pending_ids = [folder.pk]
            while pending_ids:
                child_ids = list(TestCaseFolder.objects.filter(parent_id__in=pending_ids).values_list('pk', flat=True))
                descendant_ids.update(child_ids)
                pending_ids = child_ids
            if parent.pk in descendant_ids:
                raise ValidationError({'parent': '不能将文件夹移动到自身子目录中'})
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        # 删除目录及其子目录前解除用例关联，避免用例被级联删除。
        descendant_ids = {instance.pk}
        pending_ids = [instance.pk]
        while pending_ids:
            child_ids = list(TestCaseFolder.objects.filter(parent_id__in=pending_ids).values_list('pk', flat=True))
            descendant_ids.update(child_ids)
            pending_ids = child_ids
        TestCase.objects.filter(folder_id__in=descendant_ids).update(folder=None, updated_at=timezone.now())
        instance.delete()


class TestCaseViewSet(viewsets.ModelViewSet):
    """测试用例视图集"""
    queryset = TestCase.objects.all()
    serializer_class = TestCaseSerializer
    permission_classes = [IsAuthenticated]
    # 测试用例列表需要支持前端按项目一次加载较大批量数据，同时保留分页上限保护。
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at', 'name', 'priority', 'status']
    ordering = ['-created_at']
    filterset_fields = ['project', 'status', 'priority', 'created_by']

    def get_queryset(self):
        # 只显示用户有权限访问的项目的测试用例
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()

    def get_queryset(self):
        # 只显示用户有权限访问的项目的测试用例
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        # 预取步骤及元素定位信息，保证详情序列化时能完整返回步骤回显数据。
        return TestCase.objects.filter(project__in=accessible_projects).select_related(
            'project', 'created_by', 'folder'
        ).prefetch_related('steps__element__locator_strategy')

    def _validate_steps(self, steps_data, project_id):
        """校验测试用例提交的步骤，并复用步骤序列化器的业务规则。"""
        valid_actions = {value for value, _ in TestCaseStep.ACTION_TYPE_CHOICES}
        valid_assert_types = {value for value, _ in TestCaseStep.ASSERT_TYPE_CHOICES}

        for index, raw_step in enumerate(steps_data or [], 1):
            step = dict(raw_step)
            action_type = step.get('action_type', 'click')
            assert_type = step.get('assert_type', '')
            element_id = step.get('element_id', step.get('element'))

            if action_type not in valid_actions:
                raise ValidationError({'steps': [f'步骤{index}的操作类型无效: {action_type}']})
            if assert_type and assert_type not in valid_assert_types:
                raise ValidationError({'steps': [f'步骤{index}的断言类型无效: {assert_type}']})

            element = None
            if element_id:
                element = Element.objects.filter(id=element_id, project_id=project_id).first()
                if not element:
                    raise ValidationError({'steps': [f'步骤{index}的目标元素不属于当前项目']})

            try:
                # 登录态步骤的目标资源必须与测试用例项目一致。
                action_params = step.get('action_params') or {}
                if action_type == 'storageState':
                    operation = action_params.get('operation')
                    referenced_state_id = action_params.get('storage_state_id')
                    if (
                        (operation in {'switch', 'save'} and action_params.get('mode') == 'overwrite')
                        or operation == 'switch'
                    ):
                        if not StorageState.objects.filter(
                            pk=referenced_state_id, project_id=project_id
                        ).exists():
                            raise ValidationError({'steps': [f'步骤{index}选择的登录态不存在或不属于当前项目']})
                validate_test_case_step_data(
                    action_type=action_type,
                    assert_type=assert_type,
                    input_value=step.get('input_value', ''),
                    assert_value=step.get('assert_value', ''),
                    element=element,
                    project_id=project_id,
                    action_params=action_params,
                )
            except ValidationError as exc:
                detail = exc.detail
                if isinstance(detail, list):
                    detail = detail[0]
                raise ValidationError({'steps': [f'步骤{index}{detail}']}) from exc

    def perform_create(self, serializer):
        # 创建测试用例
        project_id = self.request.data.get('project')
        self._validate_steps(self.request.data.get('steps', []), project_id)
        instance = serializer.save(created_by=self.request.user)

        # 记录操作
        log_operation('create', 'test_case', instance.id, instance.name, self.request.user)

        # 处理步骤数据
        steps_data = self.request.data.get('steps', [])
        logger.info(f"创建测试用例 {instance.id} 的步骤数据: {len(steps_data)} 个步骤")

        if 'steps' in self.request.data:
            # 创建新步骤
            created_count = 0
            for i, step_data in enumerate(steps_data):
                # 确保步骤数据结构正确
                step_data = dict(step_data)  # 创建副本避免修改原数据
                step_data['test_case'] = instance.id  # 使用测试用例ID
                step_data['step_number'] = i + 1  # 确保步骤序号正确

                # 处理元素ID
                if 'element_id' in step_data:
                    step_data['element'] = step_data.pop('element_id')

                # 移除只读字段
                step_data.pop('id', None)
                step_data.pop('element_name', None)
                step_data.pop('element_locator', None)
                step_data.pop('created_at', None)
                step_data.pop('expanded', None)  # 前端UI状态字段

                # 使用模型直接创建，避免序列化器的复杂性
                try:
                    TestCaseStep.objects.create(
                        test_case=instance,
                        step_number=step_data.get('step_number', i + 1),
                        action_type=step_data.get('action_type', 'click'),
                        element_id=step_data.get('element') if step_data.get('element') else None,
                        input_value=step_data.get('input_value', ''),
                        action_params=step_data.get('action_params') or {},
                        wait_time=step_data.get('wait_time', 1000),
                        assert_type=step_data.get('assert_type', ''),
                        assert_value=step_data.get('assert_value', ''),
                        description=step_data.get('description', '')
                    )
                    created_count += 1
                except Exception as e:
                    logger.error(f"创建步骤 {i + 1} 失败: {str(e)}")
                    logger.error(f"步骤数据: {step_data}")

            logger.info(f"成功创建了 {created_count} 个新步骤")

    @action(detail=True, methods=['post'])
    def copy_case(self, request, pk=None):
        """复制测试用例"""
        test_case = self.get_object()

        try:
            # 1. 复制测试用例基本信息
            new_case = TestCase.objects.create(
                project=test_case.project,
                name=f"{test_case.name}_copy",
                description=test_case.description,
                folder=test_case.folder,
                priority=test_case.priority,
                status=test_case.status,
                created_by=request.user
            )

            # 2. 复制测试步骤
            steps = test_case.steps.all().order_by('step_number')
            new_steps = []
            for step in steps:
                new_steps.append(TestCaseStep(
                    test_case=new_case,
                    step_number=step.step_number,
                    action_type=step.action_type,
                    element=step.element,
                    input_value=step.input_value,
                    action_params=step.action_params,
                    wait_time=step.wait_time,
                    assert_type=step.assert_type,
                    assert_value=step.assert_value,
                    description=step.description
                ))

            if new_steps:
                TestCaseStep.objects.bulk_create(new_steps)

            # 记录操作
            log_operation('create', 'test_case', new_case.id, new_case.name, request.user)

            serializer = self.get_serializer(new_case)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"复制测试用例失败: {str(e)}")
            return Response({'error': f"复制失败: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def perform_update(self, serializer):
        # 更新测试用例步骤
        project_id = self.request.data.get('project', serializer.instance.project_id)
        self._validate_steps(self.request.data.get('steps', []), project_id)
        instance = serializer.save()

        # 记录操作
        log_operation('edit', 'test_case', instance.id, instance.name, self.request.user)

        # 处理步骤数据
        steps_data = self.request.data.get('steps', [])
        logger.info(f"更新测试用例 {instance.id} 的步骤数据: {len(steps_data)} 个步骤")

        if 'steps' in self.request.data:
            # 删除现有步骤
            existing_steps_count = instance.steps.count()
            instance.steps.all().delete()
            logger.info(f"删除了 {existing_steps_count} 个现有步骤")

            # 创建新步骤
            created_count = 0
            for i, step_data in enumerate(steps_data):
                # 确保步骤数据结构正确
                step_data = dict(step_data)  # 创建副本避免修改原数据
                step_data['test_case'] = instance.id  # 使用测试用例ID
                step_data['step_number'] = i + 1  # 确保步骤序号正确

                # 处理元素ID
                if 'element_id' in step_data:
                    step_data['element'] = step_data.pop('element_id')

                # 移除只读字段
                step_data.pop('id', None)
                step_data.pop('element_name', None)
                step_data.pop('element_locator', None)
                step_data.pop('created_at', None)
                step_data.pop('expanded', None)  # 前端UI状态字段

                # 使用模型直接创建，避免序列化器的复杂性
                try:
                    TestCaseStep.objects.create(
                        test_case=instance,
                        step_number=step_data.get('step_number', i + 1),
                        action_type=step_data.get('action_type', 'click'),
                        element_id=step_data.get('element') if step_data.get('element') else None,
                        input_value=step_data.get('input_value', ''),
                        action_params=step_data.get('action_params') or {},
                        wait_time=step_data.get('wait_time', 1000),
                        assert_type=step_data.get('assert_type', ''),
                        assert_value=step_data.get('assert_value', ''),
                        description=step_data.get('description', '')
                    )
                    created_count += 1
                except Exception as e:
                    logger.error(f"创建步骤 {i + 1} 失败: {str(e)}")
                    logger.error(f"步骤数据: {step_data}")

            logger.info(f"成功创建了 {created_count} 个新步骤")

    def _generate_step_log(self, step, step_result='success'):
        """根据测试步骤生成执行日志"""
        import time

        # 模拟执行时间（0.1秒到2秒之间）
        execution_time = round(random.uniform(0.1, 2.0), 2)

        # 构建基础日志
        log_parts = []

        # 步骤信息
        if step.element:
            element_name = step.element.name
            locator_info = f"{step.element.locator_strategy.name}={step.element.locator_value}"
        else:
            element_name = "页面"
            locator_info = "无"

        # 根据操作类型生成具体日志
        if step.action_type == 'click':
            log_parts.append(f"点击元素 '{element_name}'")
            log_parts.append(f"- 使用定位器: {locator_info}")
            if step_result == 'success':
                log_parts.append(f"- 元素点击成功 - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 元素点击失败 - 元素未找到或不可点击")

        elif step.action_type == 'fill':
            log_parts.append(f"在元素 '{element_name}' 中输入文本")
            log_parts.append(f"- 使用定位器: {locator_info}")
            log_parts.append(f"- 输入值: '{step.input_value}'")
            if step_result == 'success':
                log_parts.append(f"- 文本输入成功 - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 文本输入失败 - 元素未找到或不可编辑")

        elif step.action_type == 'getText':
            log_parts.append(f"获取元素 '{element_name}' 的文本内容")
            log_parts.append(f"- 使用定位器: {locator_info}")
            if step.assert_value:
                log_parts.append(f"- 保存到临时变量: '{step.assert_value}'")
            if step_result == 'success':
                # 模拟获取到的文本
                mock_text = f"示例文本内容_{step.id}" if step.id else "示例文本内容"
                log_parts.append(f"- 获取到文本: '{mock_text}' - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 获取文本失败 - 元素未找到")

        elif step.action_type == 'waitFor':
            log_parts.append(f"等待元素 '{element_name}' 出现")
            log_parts.append(f"- 使用定位器: {locator_info}")
            log_parts.append(f"- 超时时间: {step.wait_time / 1000}秒")
            if step_result == 'success':
                log_parts.append(f"- 元素在 {execution_time}s 后出现")
            else:
                log_parts.append(f"- 等待超时 - 元素未在指定时间内出现")

        elif step.action_type == 'hover':
            log_parts.append(f"在元素 '{element_name}' 上悬停")
            log_parts.append(f"- 使用定位器: {locator_info}")
            if step_result == 'success':
                log_parts.append(f"- 悬停操作成功 - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 悬停操作失败 - 元素未找到")

        elif step.action_type == 'scroll':
            log_parts.append(f"滚动到元素 '{element_name}'")
            log_parts.append(f"- 使用定位器: {locator_info}")
            if step_result == 'success':
                log_parts.append(f"- 滚动操作成功 - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 滚动操作失败 - 元素未找到")

        elif step.action_type == 'screenshot':
            log_parts.append(f"执行截图操作")
            if step.element:
                log_parts.append(f"- 截图范围: 元素 '{element_name}'")
            else:
                log_parts.append(f"- 截图范围: 整个页面")
            if step_result == 'success':
                screenshot_name = f"screenshot_{int(time.time())}.png"
                log_parts.append(f"- 截图保存成功: {screenshot_name} - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 截图保存失败")

        elif step.action_type == 'assert':
            log_parts.append(f"执行断言验证")
            log_parts.append(f"- 断言类型: {step.assert_type}")
            if step.assert_value:
                log_parts.append(f"- 期望值: '{step.assert_value}'")
            if step_result == 'success':
                log_parts.append(f"- 断言通过 - 耗时 {execution_time}s")
            else:
                log_parts.append(f"- 断言失败 - 实际值与期望值不匹配")

        elif step.action_type == 'wait':
            log_parts.append(f"固定等待")
            log_parts.append(f"- 等待时间: {step.wait_time / 1000}秒")
            log_parts.append(f"- 等待完成")

        else:
            # 默认处理其他操作类型
            log_parts.append(f"执行操作: {step.action_type}")
            if step.element:
                log_parts.append(f"- 目标元素: {element_name}")
            if step.input_value:
                log_parts.append(f"- 输入值: {step.input_value}")
            log_parts.append(f"- 操作{'成功' if step_result == 'success' else '失败'} - 耗时 {execution_time}s")

        # 如果步骤有描述，添加到日志中
        if step.description:
            log_parts.insert(0, f"说明: {step.description}")

        return '\n'.join(log_parts)

    def _generate_failure_screenshot(self, step_number, step_description):
        """生成失败截图的模拟数据（base64格式）"""
        try:
            from PIL import Image, ImageDraw, ImageFont
            import io
            import base64

            # 创建一个模拟的失败截图
            # 实际应用中，这里应该是通过Playwright/Selenium捕获真实的页面截图
            width, height = 1280, 720
            img = Image.new('RGB', (width, height), color=(240, 240, 245))
            draw = ImageDraw.Draw(img)

            # 绘制标题区域
            draw.rectangle([0, 0, width, 80], fill=(220, 53, 69))

            # 添加文本信息（使用默认字体）
            try:
                # 尝试使用系统字体
                font_title = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 40)
                font_text = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 24)
            except:
                # 如果系统字体不可用，使用默认字体
                font_title = ImageFont.load_default()
                font_text = ImageFont.load_default()

            # 标题
            draw.text((40, 20), "测试步骤执行失败", fill=(255, 255, 255), font=font_title)

            # 失败信息
            info_y = 120
            draw.text((40, info_y), f"失败步骤: 步骤 {step_number}", fill=(50, 50, 50), font=font_text)
            draw.text((40, info_y + 40), f"步骤说明: {step_description}", fill=(50, 50, 50), font=font_text)
            draw.text((40, info_y + 80), f"失败时间: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
                      fill=(50, 50, 50), font=font_text)

            # 绘制一个模拟的浏览器窗口
            browser_y = info_y + 140
            draw.rectangle([40, browser_y, width - 40, height - 40], outline=(200, 200, 200), width=2)
            draw.rectangle([40, browser_y, width - 40, browser_y + 40], fill=(200, 200, 200))
            draw.text((60, browser_y + 10), "模拟浏览器页面 - 失败截图", fill=(80, 80, 80), font=font_text)

            # 在浏览器窗口中绘制错误提示
            error_y = browser_y + 80
            draw.text((60, error_y), "× 元素定位失败或操作执行异常", fill=(220, 53, 69), font=font_text)
            draw.text((60, error_y + 40), "× 请检查元素定位器是否正确", fill=(220, 53, 69), font=font_text)
            draw.text((60, error_y + 80), "× 或页面加载是否完成", fill=(220, 53, 69), font=font_text)

            # 转换为base64
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode()

            return f"data:image/png;base64,{img_base64}"

        except Exception as e:
            logger.error(f"生成失败截图时出错: {str(e)}")
            # 返回一个简单的错误占位符
            return None

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        """运行单个测试用例（Playwright / Selenium）。

        步骤循环、登录态动作、截图与结果落库统一复用
        ``test_executor.run_single_case``，与 MCP 异步执行同一实现；
        本视图只负责前置校验与响应组装。
        """
        from .test_executor import check_engine_environment, run_single_case

        test_case = self.get_object()
        engine_type = request.data.get('engine', 'playwright')
        browser = request.data.get('browser', 'chrome')
        headless = request.data.get('headless', False)

        if engine_type == 'selenium' and test_case.steps.filter(
                action_type='storageState').exists():
            return Response({'error': '包含登录态步骤的测试用例必须使用 Playwright 执行'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 先建记录再检查环境：环境不可用时也要留下一条 failed 记录可追溯。
        execution = TestCaseExecution.objects.create(
            test_case=test_case,
            project=test_case.project,
            execution_source='manual',
            status='running',
            engine=engine_type,
            browser=browser,
            headless=headless,
            created_by=request.user,
            started_at=timezone.now(),
        )

        is_ready, error_msg = check_engine_environment(engine_type, browser)
        if not is_ready:
            logger.error(f"{engine_type} 执行环境检查失败: {error_msg}")
            execution.status = 'failed'
            execution.error_message = error_msg
            if engine_type == 'selenium':
                execution.execution_logs = (
                    f"浏览器环境检查失败\n\n{error_msg}\n\n建议：\n"
                    f"1. 请确认已安装 {browser.capitalize()} 浏览器\n"
                    f"2. 执行 `python manage.py download_webdrivers --browsers {browser}` 安装对应驱动\n"
                    f"3. 或者尝试使用其他浏览器（Chrome、Firefox、Edge）\n"
                    f"4. 或者使用 Playwright 引擎（支持自动下载浏览器）"
                )
            else:
                execution.execution_logs = (
                    f"Playwright 浏览器环境检查失败\n\n{error_msg}\n\n建议：\n"
                    f"1. 执行 `python -m playwright install` 安装浏览器\n"
                    f"2. 如果 Playwright 模块未安装，请先执行 `pip install playwright`\n"
                    f"3. 或者切换到 Selenium 引擎并安装对应浏览器驱动"
                )
            execution.finished_at = timezone.now()
            execution.save()

            return Response({
                'success': False,
                'logs': execution.execution_logs,
                'screenshots': [],
                'execution_time': 0,
                'errors': [{
                    'message': f'{browser.capitalize()} 执行环境不可用',
                    'details': error_msg,
                    'step_number': None,
                    'action_type': '浏览器检查',
                    'element': '',
                    'description': '执行前浏览器与驱动环境检查'
                }]
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            case_result = run_single_case(
                test_case,
                engine=engine_type,
                browser=browser,
                headless=headless,
                executed_by=request.user,
                execution=execution,
            )
        except Exception as exc:  # noqa: BLE001 - 兜底：执行体异常也要返回结构化结果
            logger.exception('执行测试用例失败: %s', exc)
            return Response({
                'success': False,
                'logs': f"执行失败: {exc}",
                'screenshots': [],
                'execution_time': 0,
                'errors': [{'message': f'执行失败: {exc}', 'details': ''}],
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 失败步骤转成前端使用的结构化错误（含中文动作名与元素名）。
        action_labels = dict(TestCaseStep.ACTION_TYPE_CHOICES)
        step_count = test_case.steps.count()
        element_names = {
            step.step_number: (step.element.name if step.element else '')
            for step in test_case.steps.select_related('element')
        }
        errors = []
        for step_result in case_result.get('steps') or []:
            if step_result.get('success', True):
                continue
            number = step_result.get('step_number')
            action_type = step_result.get('action_type') or ''
            errors.append({
                'message': f"步骤 {number}/{step_count} 执行失败",
                'details': step_result.get('error') or '',
                'step_number': number,
                'action_type': action_labels.get(action_type, action_type),
                'element': element_names.get(number, ''),
                'description': step_result.get('description') or '',
            })
        if not errors and execution.error_message:
            errors.append({'message': execution.error_message, 'details': ''})

        log_operation('run', 'test_case', test_case.id, test_case.name, request.user)

        return Response({
            'success': execution.status == 'passed',
            'logs': execution.execution_logs,
            'screenshots': case_result.get('screenshots') or [],
            'execution_time': execution.execution_time,
            'errors': errors,
        })

    @action(detail=False, methods=['post'])
    def batch_run(self, request):
        """批量运行测试用例。

        每个用例独立异步执行并立即返回执行记录 ID（不再伪造 passed），
        结果用 ``/test-case-executions/{id}/`` 轮询。
        """
        from .mcp_runner import start_case_execution

        test_case_ids = request.data.get('test_case_ids', [])
        if not test_case_ids:
            return Response({'error': '请选择要运行的测试用例'},
                            status=status.HTTP_400_BAD_REQUEST)

        engine = request.data.get('engine', 'playwright')
        browser = request.data.get('browser', 'chrome')
        headless = request.data.get('headless', True)

        results = []
        for test_case_id in test_case_ids:
            test_case = self.get_queryset().filter(id=test_case_id).first()
            if not test_case:
                results.append({
                    'test_case_id': test_case_id,
                    'test_case_name': '未知',
                    'status': 'error',
                    'error': '测试用例不存在或无权限',
                })
                continue
            try:
                execution = start_case_execution(
                    test_case, request.user, engine=engine,
                    browser=browser, headless=headless)
            except ValueError as exc:
                results.append({
                    'test_case_id': test_case.id,
                    'test_case_name': test_case.name,
                    'status': 'error',
                    'error': str(exc),
                })
                continue
            results.append({
                'test_case_id': test_case.id,
                'test_case_name': test_case.name,
                'status': execution.status,
                'execution_id': execution.id,
            })

        return Response({'results': results})

    def perform_destroy(self, instance):
        # 记录操作（在删除前记录）
        log_operation('delete', 'test_case', instance.id, instance.name, self.request.user)
        instance.delete()


class TestCaseStepViewSet(viewsets.ModelViewSet):
    """测试用例步骤视图集"""
    queryset = TestCaseStep.objects.all()
    serializer_class = TestCaseStepSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ['step_number']
    ordering = ['step_number']
    filterset_fields = ['test_case', 'action_type']

    def get_queryset(self):
        # 只显示用户有权限访问的测试用例的步骤
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        accessible_test_cases = TestCase.objects.filter(project__in=accessible_projects)
        return TestCaseStep.objects.filter(test_case__in=accessible_test_cases)

    def perform_create(self, serializer):
        if not serializer.validated_data.get('test_case'):
            raise ValidationError({'test_case': '创建步骤时必须指定可访问的测试用例'})
        serializer.save()


class TestCaseExecutionViewSet(viewsets.ModelViewSet):
    """测试用例执行记录视图集"""
    queryset = TestCaseExecution.objects.all().select_related(
        'test_case', 'project', 'test_suite', 'executed_by'
    )
    serializer_class = TestCaseExecutionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['test_case__name', 'error_message']
    ordering_fields = ['created_at', 'started_at', 'finished_at', 'status']
    ordering = ['-created_at']
    filterset_fields = ['project', 'test_suite', 'test_case', 'status', 'execution_source']
    pagination_class = StandardPagination

    def get_queryset(self):
        # 只显示用户有权限访问的项目的执行记录
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return TestCaseExecution.objects.filter(
            project__in=accessible_projects
        ).select_related(
            'test_case', 'project', 'test_suite', 'created_by'
        )

    def perform_destroy(self, instance):
        # 记录操作
        name = instance.test_case.name if instance.test_case else f"执行记录#{instance.id}"
        log_operation('delete', 'report', instance.id, name, self.request.user)
        instance.delete()

    @action(detail=False, methods=['post'], url_path='batch-delete')
    def batch_delete(self, request):
        """批量删除执行记录"""
        try:
            ids = request.data.get('ids', [])

            # 验证ids参数
            if not ids:
                return Response({'error': '未提供要删除的记录ID'}, status=status.HTTP_400_BAD_REQUEST)

            # 确保ids是列表
            if not isinstance(ids, list):
                return Response({'error': 'ids参数格式错误，应为数组'}, status=status.HTTP_400_BAD_REQUEST)

            # 确保只能删除有权限的记录
            queryset = self.get_queryset()
            records_to_delete = queryset.filter(id__in=ids)

            # 检查是否有记录可删除
            if not records_to_delete.exists():
                return Response({'error': '未找到可删除的记录或没有权限删除'}, status=status.HTTP_404_NOT_FOUND)

            # 获取可删除记录的ID列表，避免对带select_related的queryset调用delete()可能出现的问题
            deletable_ids = list(records_to_delete.values_list('id', flat=True))

            # 使用ID列表直接删除
            deleted_count = TestCaseExecution.objects.filter(id__in=deletable_ids).delete()[0]

            return Response({'message': f'成功删除 {deleted_count} 条记录', 'deleted_count': deleted_count})
        except Exception as e:
            logger.error(f"批量删除测试用例执行记录失败: {str(e)}", exc_info=True)
            return Response({'error': f'批量删除失败: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class OperationRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """操作记录视图集（只读）"""
    queryset = OperationRecord.objects.all()
    serializer_class = OperationRecordSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['operation_type', 'resource_type', 'user']

    def get_queryset(self):
        # 返回最近的操作记录，按创建时间倒序
        # 过滤掉AI智能模式相关的操作记录
        queryset = OperationRecord.objects.exclude(
            resource_type__in=['ai_case', 'ai_execution']
        ).order_by('-created_at')

        # 支持通过查询参数限制返回数量
        limit = self.request.query_params.get('limit', None)
        if limit:
            try:
                limit = int(limit)
                queryset = queryset[:limit]
            except ValueError:
                pass

        return queryset


# ==================== 定时任务和通知相关视图 ====================

class UiScheduledTaskViewSet(viewsets.ModelViewSet):
    """UI定时任务视图集"""
    queryset = UiScheduledTask.objects.all()
    serializer_class = UiScheduledTaskSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['task_type', 'status', 'trigger_type', 'project']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'next_run_time', 'last_run_time']
    ordering = ['-created_at']

    def get_queryset(self):
        """只显示用户有权限访问的项目的定时任务"""
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        return UiScheduledTask.objects.filter(project__in=accessible_projects)

    def perform_create(self, serializer):
        """创建定时任务"""
        instance = serializer.save(created_by=self.request.user)
        log_operation('create', 'scheduled_task', instance.id, instance.name, self.request.user)

    def perform_update(self, serializer):
        """更新定时任务"""
        instance = serializer.save()
        log_operation('edit', 'scheduled_task', instance.id, instance.name, self.request.user)

    def perform_destroy(self, instance):
        """删除定时任务"""
        log_operation('delete', 'scheduled_task', instance.id, instance.name, self.request.user)
        instance.delete()

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """暂停定时任务"""
        task = self.get_object()
        task.status = 'PAUSED'
        task.save()
        return Response({'message': '任务已暂停', 'status': task.status})

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        """恢复定时任务"""
        task = self.get_object()
        task.status = 'ACTIVE'
        task.next_run_time = task.calculate_next_run()
        task.save()
        return Response({'message': '任务已恢复', 'status': task.status})

    @action(detail=True, methods=['post'])
    def run_now(self, request, pk=None):
        """立即运行任务"""
        task = self.get_object()

        try:
            # 更新任务执行时间和次数
            task.last_run_time = timezone.now()
            task.total_runs += 1
            # 重新计算下次运行时间
            task.next_run_time = task.calculate_next_run()
            task.save()

            # 根据任务类型执行不同的逻辑
            if task.task_type == 'TEST_SUITE':
                # 执行测试套件
                if not task.test_suite:
                    return Response({
                        'error': '该任务未配置测试套件'
                    }, status=status.HTTP_400_BAD_REQUEST)

                test_suite = task.test_suite
                test_case_count = test_suite.suite_test_cases.count()

                if test_case_count == 0:
                    return Response({
                        'error': '该测试套件未包含任何测试用例，无法执行'
                    }, status=status.HTTP_400_BAD_REQUEST)

                if task.engine == 'playwright':
                    from .playwright_engine import PlaywrightTestEngine
                    is_ready, error_msg = PlaywrightTestEngine.check_execution_environment_sync(task.browser)
                    if not is_ready:
                        return Response({
                            'error': error_msg,
                            'message': 'Playwright 浏览器未就绪，请先安装后再执行测试套件任务'
                        }, status=status.HTTP_400_BAD_REQUEST)
                elif task.storage_state_id:
                    return Response({'error': '只有 Playwright 任务支持登录态'}, status=status.HTTP_400_BAD_REQUEST)

                # 更新套件执行状态
                test_suite.execution_status = 'running'
                test_suite.save()

                # 在后台线程中执行测试
                import threading
                from .test_executor import TestExecutor

                def run_test():
                    try:
                        executor = TestExecutor(
                            test_suite=test_suite,
                            engine=task.engine,
                            browser=task.browser,
                            headless=task.headless,
                            executed_by=task.created_by,
                            storage_state_id=task.storage_state_id
                        )
                        executor.run()

                        # 更新任务执行结果
                        task.successful_runs += 1
                        task.last_result = {'status': 'success', 'message': '测试套件执行成功'}
                        task.error_message = ''
                        task.save()

                        # 发送成功通知
                        self._send_task_notification(task, success=True)

                    except Exception as e:
                        task.failed_runs += 1
                        task.last_result = {'status': 'failed', 'message': str(e)}
                        task.error_message = str(e)
                        test_suite.execution_status = 'failed'
                        test_suite.save()
                        task.save()

                        # 发送失败通知
                        self._send_task_notification(task, success=False)

                # 启动后台线程执行测试
                thread = threading.Thread(target=run_test)
                thread.daemon = True
                thread.start()

                log_operation('run', 'scheduled_task', task.id, task.name, request.user)

                return Response({
                    'message': '测试套件开始执行',
                    'task_id': task.id,
                    'task_name': task.name,
                    'test_suite': test_suite.name,
                    'test_case_count': test_case_count,
                    'engine': task.engine,
                    'browser': task.browser,
                    'headless': task.headless
                }, status=status.HTTP_200_OK)

            elif task.task_type == 'TEST_CASE':
                # 执行测试用例
                if not task.test_cases or len(task.test_cases) == 0:
                    return Response({
                        'error': '该任务未配置测试用例'
                    }, status=status.HTTP_400_BAD_REQUEST)

                test_case_ids = task.test_cases
                test_cases = TestCase.objects.filter(id__in=test_case_ids).select_related('project')
                test_case_count = test_cases.count()

                if test_case_count == 0:
                    return Response({
                        'error': '找不到配置的测试用例'
                    }, status=status.HTTP_400_BAD_REQUEST)

                if task.engine == 'selenium' and TestCaseStep.objects.filter(
                    test_case_id__in=test_case_ids, action_type='storageState'
                ).exists():
                    return Response({'error': '包含登录态步骤的任务必须使用 Playwright 执行'}, status=status.HTTP_400_BAD_REQUEST)

                if task.engine == 'playwright':
                    from .playwright_engine import PlaywrightTestEngine
                    is_ready, error_msg = PlaywrightTestEngine.check_execution_environment_sync(task.browser)
                    if not is_ready:
                        return Response({
                            'error': error_msg,
                            'message': 'Playwright 浏览器未就绪，请先安装后再执行测试用例任务'
                        }, status=status.HTTP_400_BAD_REQUEST)
                elif task.storage_state_id:
                    return Response({'error': '只有 Playwright 任务支持登录态'}, status=status.HTTP_400_BAD_REQUEST)

                if task.storage_state_id and not StorageState.objects.filter(
                        pk=task.storage_state_id, project=task.project).exists():
                    return Response({'error': '登录态不存在或不属于任务关联项目'},
                                    status=status.HTTP_400_BAD_REQUEST)

                # 在后台线程中执行测试用例
                import threading
                from .test_executor import run_single_case

                def run_test_cases():
                    """在后台线程中执行测试用例"""
                    success_count = 0
                    failed_count = 0

                    try:
                        for test_case in test_cases:
                            # 执行记录由本视图预建，执行体统一复用 run_single_case。
                            execution = TestCaseExecution.objects.create(
                                test_case=test_case,
                                project=task.project,
                                execution_source='scheduled',
                                status='running',
                                engine=task.engine,
                                browser=task.browser,
                                headless=task.headless,
                                created_by=task.created_by,
                                started_at=timezone.now(),
                            )
                            try:
                                case_result = run_single_case(
                                    test_case,
                                    engine=task.engine,
                                    browser=task.browser,
                                    headless=task.headless,
                                    executed_by=task.created_by,
                                    execution=execution,
                                    storage_state_id=task.storage_state_id,
                                )
                            except Exception as exc:  # noqa: BLE001 - 单用例失败不影响同批其他用例
                                logger.error(
                                    f"执行测试用例 {test_case.name} 时发生异常: {exc}")
                                execution.status = 'failed'
                                execution.error_message = str(exc)
                                execution.finished_at = timezone.now()
                                execution.save()
                                failed_count += 1
                                continue

                            if case_result.get('status') == 'passed':
                                success_count += 1
                                logger.info(f"测试用例 {test_case.name} 执行成功")
                            else:
                                failed_count += 1
                                logger.warning(
                                    f"测试用例 {test_case.name} 执行失败: "
                                    f"{case_result.get('error')}")

                        # 更新任务执行结果
                        if failed_count == 0:
                            task.successful_runs += 1
                            task.last_result = {
                                'status': 'success',
                                'message': f'执行完成: {success_count}个成功',
                                'success_count': success_count,
                                'failed_count': failed_count
                            }
                            task.error_message = ''
                            task.save()

                            # 发送成功通知
                            self._send_task_notification(task, success=True)
                        else:
                            task.failed_runs += 1
                            task.last_result = {
                                'status': 'partial',
                                'message': f'执行完成: {success_count}个成功, {failed_count}个失败',
                                'success_count': success_count,
                                'failed_count': failed_count
                            }
                            task.error_message = f'{failed_count}个测试用例执行失败'
                            task.save()

                            # 发送失败通知
                            self._send_task_notification(task, success=False)

                    except Exception as e:
                        logger.error(f"执行定时任务测试用例时发生异常: {str(e)}")
                        task.failed_runs += 1
                        task.last_result = {'status': 'failed', 'message': str(e)}
                        task.error_message = str(e)
                        task.save()

                        # 发送失败通知
                        self._send_task_notification(task, success=False)

                # 启动后台线程执行测试
                thread = threading.Thread(target=run_test_cases)
                thread.daemon = True
                thread.start()

                log_operation('run', 'scheduled_task', task.id, task.name, request.user)

                return Response({
                    'message': '测试用例开始执行',
                    'task_id': task.id,
                    'task_name': task.name,
                    'test_case_count': test_case_count,
                    'engine': task.engine,
                    'browser': task.browser,
                    'headless': task.headless
                }, status=status.HTTP_200_OK)

            else:
                return Response({
                    'error': '不支持的任务类型'
                }, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f'执行定时任务失败: {str(e)}')
            return Response({
                'error': f'执行失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _send_task_notification(self, task, success):
        """发送任务执行通知"""
        try:
            logger.info(f"准备发送任务 {task.id} 的通知，执行结果: {'成功' if success else '失败'}")

            # 检查是否需要发送通知
            if success and not task.notify_on_success:
                logger.info("任务执行成功但未启用成功通知")
                return

            if not success and not task.notify_on_failure:
                logger.info("任务执行失败但未启用失败通知")
                return

            # 检查通知类型
            if not task.notification_type:
                logger.info("未设置通知类型")
                return

            logger.info(f"通知类型: {task.notification_type}")

            # 根据通知类型发送不同的通知
            if task.notification_type in ['webhook', 'both']:
                logger.info("发送Webhook通知")
                self._send_webhook_notification(task, success)

            if task.notification_type in ['email', 'both']:
                logger.info("发送邮件通知")
                self._send_email_notification(task, success)

        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}", exc_info=True)

    def _send_webhook_notification(self, task, success):
        """发送Webhook通知"""
        try:
            import requests
            import json

            logger.info("=== 开始发送Webhook通知 ===")

            # 使用统一的通知配置
            try:
                from apps.core.models import UnifiedNotificationConfig
                all_webhook_configs = UnifiedNotificationConfig.objects.filter(
                    config_type__in=['webhook_wechat', 'webhook_feishu', 'webhook_dingtalk', 'webhook_slack'],
                    is_active=True
                )
                logger.info("使用统一通知配置 (UnifiedNotificationConfig)")
            except ImportError as e:
                # 如果 core 模块不可用，记录错误并返回
                logger.error(f"无法导入统一通知配置: {e}")
                logger.warning("通知发送失败：无法找到通知配置模块")
                return
            except Exception as e:
                logger.error(f"获取通知配置时出错: {e}")
                return

            all_webhook_bots = []
            for config in all_webhook_configs:
                bots = config.get_webhook_bots()
                if bots:
                    for bot in bots:
                        # 只添加启用了"UI自动化测试"的机器人
                        if bot.get('enabled', True) and bot.get('enable_ui_automation', True):
                            all_webhook_bots.append(bot)
                            logger.info(f"添加机器人: {bot.get('name')} (UI自动化测试已启用)")
                        elif bot.get('enabled', True):
                            logger.info(f"配置中心机器人 {bot.get('name')} 未启用UI自动化测试，跳过")

            if not all_webhook_bots:
                logger.warning("没有找到任何启用的webhook机器人配置")
                return

            logger.info(f"找到 {len(all_webhook_bots)} 个启用的webhook机器人配置")

            # 准备通知内容
            status_text = '成功' if success else '失败'
            task_type_text = '测试套件执行' if task.task_type == 'TEST_SUITE' else '测试用例执行'

            # 获取最后执行结果的详细信息
            last_result = task.last_result or {}
            result_message = last_result.get('message', '')
            success_count = last_result.get('success_count', 0)
            failed_count = last_result.get('failed_count', 0)

            # 为不同的机器人平台准备消息格式
            for bot in all_webhook_bots:
                if not bot.get('enabled', True) or not bot.get('webhook_url'):
                    logger.info(f"跳过未启用或无URL的机器人: {bot.get('name', 'Unknown')}")
                    continue

                bot_type = bot.get('type', 'unknown')
                webhook_url = bot['webhook_url']
                logger.info(f"发送通知到 {bot_type} 机器人: {bot.get('name', 'Unknown')}")

                # 构造详细内容
                # 转换执行时间到本地时区
                local_run_time = timezone.localtime(task.last_run_time).strftime(
                    '%Y-%m-%d %H:%M:%S') if task.last_run_time else '未知'
                detail_content = f"""任务名称: {task.name}

执行状态: {status_text}

执行时间: {local_run_time}

任务类型: {task_type_text}

执行引擎: {task.engine.upper()}

浏览器: {task.browser.capitalize()}"""

                if result_message:
                    detail_content += f"\n\n执行结果: {result_message}"

                if success_count > 0 or failed_count > 0:
                    detail_content += f"\n\n成功: {success_count} 个，失败: {failed_count} 个"

                # 根据机器人类型构造消息格式
                if bot_type == 'wechat':  # 企业微信
                    message_data = {
                        "msgtype": "markdown",
                        "markdown": {
                            "content": f"""**UI自动化定时任务执行{status_text}**

{detail_content}"""
                        }
                    }
                elif bot_type == 'feishu':  # 飞书
                    message_data = {
                        "msg_type": "interactive",
                        "card": {
                            "elements": [{
                                "tag": "div",
                                "text": {
                                    "content": f"**UI自动化定时任务执行{status_text}**\n\n{detail_content}",
                                    "tag": "lark_md"
                                }
                            }],
                            "header": {
                                "title": {
                                    "content": f"UI自动化定时任务执行{status_text}",
                                    "tag": "plain_text"
                                },
                                "template": "green" if success else "red"
                            }
                        }
                    }
                elif bot_type == 'dingtalk':  # 钉钉
                    message_data = {
                        "msgtype": "markdown",
                        "markdown": {
                            "title": f"UI自动化定时任务执行{status_text}",
                            "text": f"""**UI自动化定时任务执行{status_text}**

{detail_content}"""
                        }
                    }

                    # 钉钉机器人签名验证
                    secret = bot.get('secret')
                    if secret:
                        import time
                        import hmac
                        import hashlib
                        import base64
                        import urllib.parse

                        timestamp = str(round(time.time() * 1000))
                        string_to_sign = f'{timestamp}\n{secret}'
                        string_to_sign_enc = string_to_sign.encode('utf-8')
                        secret_enc = secret.encode('utf-8')
                        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
                        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

                        # 在URL中添加签名参数
                        if '?' in webhook_url:
                            webhook_url += f'&timestamp={timestamp}&sign={sign}'
                        else:
                            webhook_url += f'?timestamp={timestamp}&sign={sign}'
                elif bot_type == 'slack':  # Slack Incoming Webhook
                    # Slack Incoming Webhook 接收简单的 text 载荷。
                    message_data = {
                        "text": f"UI自动化定时任务执行{status_text}\n\n{detail_content}"
                    }
                else:
                    logger.warning(f"未知的机器人类型: {bot_type}")
                    continue

                # 发送webhook请求
                try:
                    logger.info(f"发送请求到: {webhook_url}")
                    logger.info(f"消息数据: {json.dumps(message_data, ensure_ascii=False, indent=2)}")

                    response = requests.post(
                        webhook_url,
                        json=message_data,
                        headers={'Content-Type': 'application/json'},
                        timeout=10
                    )

                    logger.info(f"响应状态码: {response.status_code}")
                    logger.info(f"响应内容: {response.text}")

                    if response.status_code == 200:
                        logger.info(f"成功发送通知到 {bot.get('name', 'Unknown')}")

                        # 记录通知日志
                        UiNotificationLog.objects.create(
                            task=task,
                            task_name=task.name,
                            task_type=task.task_type,
                            notification_type='task_execution',
                            sender_name='系统Webhook通知',
                            sender_email='system@notification.com',
                            recipient_info=[{'name': bot.get('name', 'Unknown'), 'webhook_url': webhook_url}],
                            webhook_bot_info=bot,
                            notification_content=json.dumps(message_data, ensure_ascii=False),
                            status='success',
                            response_info={'status_code': response.status_code, 'response': response.text},
                            sent_at=timezone.now()
                        )
                    else:
                        logger.error(f"发送通知失败，状态码: {response.status_code}, 响应: {response.text}")

                        # 记录失败日志
                        UiNotificationLog.objects.create(
                            task=task,
                            task_name=task.name,
                            task_type=task.task_type,
                            notification_type='task_execution',
                            sender_name='系统Webhook通知',
                            sender_email='system@notification.com',
                            recipient_info=[{'name': bot.get('name', 'Unknown'), 'webhook_url': webhook_url}],
                            webhook_bot_info=bot,
                            notification_content=json.dumps(message_data, ensure_ascii=False),
                            status='failed',
                            error_message=f'HTTP {response.status_code}: {response.text}',
                            response_info={'status_code': response.status_code, 'response': response.text}
                        )

                except requests.exceptions.RequestException as e:
                    logger.error(f"发送webhook请求失败: {str(e)}")

                    # 记录失败日志
                    UiNotificationLog.objects.create(
                        task=task,
                        task_name=task.name,
                        task_type=task.task_type,
                        notification_type='task_execution',
                        sender_name='系统Webhook通知',
                        sender_email='system@notification.com',
                        recipient_info=[{'name': bot.get('name', 'Unknown'), 'webhook_url': webhook_url}],
                        webhook_bot_info=bot,
                        notification_content=json.dumps(message_data, ensure_ascii=False),
                        status='failed',
                        error_message=str(e)
                    )

        except Exception as e:
            logger.error(f"发送Webhook通知失败: {str(e)}", exc_info=True)

    def _send_email_notification(self, task, success):
        """发送邮件通知"""
        try:
            from django.core.mail import send_mail
            from django.conf import settings

            logger.info("=== 开始发送邮件通知 ===")

            # 获取收件人列表
            recipients = []
            if task.notify_emails:
                if isinstance(task.notify_emails, list):
                    recipients = task.notify_emails
                else:
                    recipients = [task.notify_emails]

            if not recipients:
                logger.warning("没有找到任何邮件收件人")
                return

            # 准备邮件内容
            status_text = '成功' if success else '失败'
            task_type_text = '测试套件执行' if task.task_type == 'TEST_SUITE' else '测试用例执行'

            subject = f"UI自动化定时任务执行{status_text}: {task.name}"

            last_result = task.last_result or {}
            result_message = last_result.get('message', '')

            # 转换执行时间到本地时区
            local_run_time = timezone.localtime(task.last_run_time).strftime(
                '%Y-%m-%d %H:%M:%S') if task.last_run_time else '未知'

            message = f"""
任务名称: {task.name}
执行状态: {status_text}
执行时间: {local_run_time}
任务类型: {task_type_text}
执行引擎: {task.engine.upper()}
浏览器: {task.browser.capitalize()}

执行结果:
{result_message if result_message else '无详细信息'}

错误信息:
{task.error_message if task.error_message else '无错误信息'}
            """

            # 发送邮件
            from_email = settings.DEFAULT_FROM_EMAIL
            logger.info(f"准备发送邮件，发件人: {from_email}, 收件人: {recipients}")

            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=recipients,
                fail_silently=False,
            )
            logger.info("邮件发送成功")

            # 记录通知日志
            UiNotificationLog.objects.create(
                task=task,
                task_name=task.name,
                task_type=task.task_type,
                notification_type='task_execution',
                sender_name='系统邮件通知',
                sender_email=from_email,
                recipient_info=[{'email': email} for email in recipients],
                notification_content=message,
                status='success',
                sent_at=timezone.now()
            )

        except Exception as e:
            logger.error(f"发送邮件通知失败: {str(e)}", exc_info=True)

            # 记录失败日志
            try:
                UiNotificationLog.objects.create(
                    task=task,
                    task_name=task.name,
                    task_type=task.task_type,
                    notification_type='task_execution',
                    sender_name='系统邮件通知',
                    sender_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_info=[{'email': email} for email in recipients] if recipients else [],
                    notification_content=f"发送邮件通知失败: {str(e)}",
                    status='failed',
                    error_message=str(e)
                )
            except:
                pass


class UiNotificationLogViewSet(viewsets.ReadOnlyModelViewSet):
    """UI通知日志视图集（只读）"""
    queryset = UiNotificationLog.objects.all()
    serializer_class = UiNotificationLogSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'notification_type']
    search_fields = ['task_name', 'notification_content']
    ordering_fields = ['created_at', 'sent_at']
    ordering = ['-created_at']

    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        """重试发送通知"""
        log = self.get_object()
        if log.status == 'failed':
            # 这里应该触发实际的重试逻辑
            log.retry_count += 1
            log.is_retried = True
            log.save()
            return Response({'message': '通知已加入重试队列'})
        return Response({'error': '只能重试失败的通知'}, status=status.HTTP_400_BAD_REQUEST)


class UiTaskNotificationSettingViewSet(viewsets.ModelViewSet):
    """UI任务通知设置视图集"""
    queryset = UiTaskNotificationSetting.objects.all()
    serializer_class = UiTaskNotificationSettingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['task', 'is_enabled', 'notification_type']


class AICaseViewSet(viewsets.ModelViewSet):
    queryset = AICase.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = AICaseSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['project']
    search_fields = ['name', 'description', 'task_description']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        # 返回用户有权限的项目下的AI用例，以及没有关联项目的AI用例
        return AICase.objects.filter(
            models.Q(project__in=accessible_projects) | models.Q(project__isnull=True)
        ).distinct()

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()

    def perform_destroy(self, instance):
        instance.delete()

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        """执行 AI 用例"""
        ai_case = self.get_object()

        # 创建执行记录
        execution_record = AIExecutionRecord.objects.create(
            project=ai_case.project,
            ai_case=ai_case,
            case_name=ai_case.name,
            task_description=ai_case.task_description,
            status='running',
            executed_by=request.user,
            logs="正在分析任务...\n"
        )

        # 异步执行
        import threading
        import os
        from asgiref.sync import sync_to_async
        from django.db import connection, DatabaseError
        from .ai_agent import run_full_process_sync

        def run_task():
            # 注册停止信号
            STOP_SIGNALS[execution_record.id] = False

            # 关键修复：关闭旧连接，避免子线程共享主线程的连接
            try:
                connection.close()
            except:
                pass

            # 设置环境变量，允许在后台线程中使用同步 ORM
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'

            def safe_save(record, update_fields=None, max_retries=3):
                """安全的保存方法，带有重试机制"""
                for attempt in range(max_retries):
                    try:
                        record.save(update_fields=update_fields)
                        return True
                    except (DatabaseError, Exception) as e:
                        error_str = str(e)
                        # 检查是否是MySQL连接错误
                        if '2006' in error_str or 'MySQL server has gone away' in error_str or '0' == error_str:
                            if attempt < max_retries - 1:
                                logger.warning(f"数据库连接失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                                # 关闭旧连接并重试
                                try:
                                    connection.close()
                                except:
                                    pass
                                import time
                                time.sleep(0.5)  # 等待一下再重试
                                continue
                            else:
                                logger.error(f"数据库保存失败，已达最大重试次数: {e}")
                                raise
                        else:
                            # 其他错误直接抛出
                            logger.error(f"数据库保存失败: {e}")
                            raise
                return False

            try:
                def should_stop():
                    return STOP_SIGNALS.get(execution_record.id, False)

                async def on_analysis_complete(planned_tasks):
                    execution_record.planned_tasks = planned_tasks
                    execution_record.logs += "任务分析完成，开始执行...\n"
                    await sync_to_async(safe_save)(execution_record, update_fields=['planned_tasks', 'logs'])

                async def on_step_update(step_info):
                    try:
                        # 处理日志
                        if step_info.get('type') == 'log':
                            content = step_info.get('content')
                            if content:
                                execution_record.logs += content
                                await sync_to_async(safe_save)(execution_record, update_fields=['logs'])
                            return

                        # 处理任务状态
                        task_id = step_info.get('task_id')
                        status = step_info.get('status')
                        if task_id and status:
                            if str(status).strip().lower() == 'completed':
                                backfilled_ids = backfill_prior_pending_tasks(
                                    execution_record.planned_tasks,
                                    task_id
                                )
                                if backfilled_ids:
                                    execution_record.logs += (
                                        f"\n[System] 已补齐遗漏标记的前序子任务: "
                                        f"{', '.join(map(str, backfilled_ids))}"
                                    )
                            updated = update_planned_task_status(
                                execution_record.planned_tasks,
                                task_id,
                                status
                            )
                            if updated:
                                update_fields = ['planned_tasks']
                                if str(status).strip().lower() == 'completed' and 'backfilled_ids' in locals() and backfilled_ids:
                                    update_fields.append('logs')
                                await sync_to_async(safe_save)(execution_record, update_fields=update_fields)
                    except Exception as e:
                        logger.error(f"更新步骤状态失败: {e}")

                history = run_full_process_sync(
                    ai_case.task_description,
                    analysis_callback=on_analysis_complete,
                    step_callback=on_step_update,
                    should_stop=should_stop
                )

                # 检查是否是手动停止
                if should_stop():
                    execution_record.status = 'stopped'
                    execution_record.logs += "\n[System] 任务已由用户停止。"
                else:
                    # 根据执行过程判断最终状态（任务或步骤出现失败则标记失败）
                    failed = False
                    if execution_record.planned_tasks:
                        failed = any(t.get('status') in ('failed', 'error') for t in execution_record.planned_tasks)
                    # history 也可能包含状态信息
                    if not failed and history:
                        try:
                            steps = history.steps if hasattr(history, 'steps') else []
                            for step in steps:
                                if getattr(step, 'status', None) == 'failed':
                                    failed = True
                                    break
                        except Exception:
                            pass

                    if failed:
                        execution_record.status = 'failed'
                        execution_record.logs += "\n执行完成，但检测到失败步骤或任务。"
                    else:
                        execution_record.status = 'passed'
                        execution_record.logs += "\n执行完成。"

                    # 记录任务完成统计信息
                    if execution_record.planned_tasks:
                        total_tasks = len(execution_record.planned_tasks)
                        completed_tasks = len(
                            [t for t in execution_record.planned_tasks if t.get('status') == 'completed'])
                        pending_tasks = len([t for t in execution_record.planned_tasks if t.get('status') == 'pending'])
                        logger.info(
                            f"🏁 Task completion summary: {completed_tasks}/{total_tasks} tasks completed, {pending_tasks} pending")

                execution_record.end_time = timezone.now()
                execution_record.duration = (execution_record.end_time - execution_record.start_time).total_seconds()

                # 格式化 history 为日志 (如果不是停止状态)
                steps = []
                if history:
                    if hasattr(history, 'steps'):
                        steps = [extract_step_info(s, i) for i, s in enumerate(history.steps)]

                execution_record.steps_completed = steps

                # 自动标记已完成的任务
                if execution_record.planned_tasks:
                    self._auto_mark_completed_tasks(execution_record)
                    execution_record.logs = append_execution_summary(
                        execution_record.logs,
                        summarize_planned_tasks(execution_record.planned_tasks)
                    )

                # 处理GIF录制文件
                self._process_gif_recording(execution_record, history)

                safe_save(execution_record)

            except Exception as e:
                error_message = str(e)
                failed_task_id = None if is_infrastructure_failure(error_message) else mark_first_active_task(execution_record.planned_tasks, 'failed')
                execution_record.status = 'failed'
                execution_record.end_time = timezone.now()
                execution_record.duration = (execution_record.end_time - execution_record.start_time).total_seconds()
                if 'Execution LLM unavailable' in error_message:
                    execution_record.logs += f"\n执行出错: AI 执行模型连接失败。{error_message}"
                else:
                    execution_record.logs += f"\n执行出错: {error_message}"
                if failed_task_id is not None:
                    execution_record.logs += f"\n[System] 子任务 {failed_task_id} 已自动标记为失败。"
                execution_record.logs = append_execution_summary(
                    execution_record.logs,
                    summarize_planned_tasks(execution_record.planned_tasks)
                )
                try:
                    safe_save(execution_record)
                except:
                    # 如果保存失败，至少尝试保存基本信息
                    logger.error(f"保存失败状态时出错: {e}")
                    pass
            finally:
                # 清理停止信号
                if execution_record.id in STOP_SIGNALS:
                    del STOP_SIGNALS[execution_record.id]

        thread = threading.Thread(target=run_task)
        thread.daemon = True
        thread.start()

        return Response({
            'message': 'AI 用例开始执行',
            'execution_id': execution_record.id
        })

    def _process_gif_recording(self, execution_record, history):
        """
        处理GIF录制文件
        在执行完成后查找生成的GIF文件并保存路径到数据库
        """
        try:
            import os
            from django.conf import settings
            from datetime import datetime

            # browser-use 默认生成的GIF文件名（固定为agent_history.gif）
            default_gif_path = os.path.join(os.getcwd(), 'agent_history.gif')

            # 如果找到GIF文件，移动到media/ai_recording目录并重命名
            if os.path.exists(default_gif_path):
                import shutil

                # 创建录制文件目录
                gif_dir = os.path.join(settings.MEDIA_ROOT, 'ai_recording')
                os.makedirs(gif_dir, exist_ok=True)

                # 生成新的文件名：用例名称+年月日时分秒
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                # 清理用例名称中的非法字符
                safe_case_name = "".join(
                    [c if c.isalnum() or c in (' ', '_', '-') else '_' for c in execution_record.case_name])
                new_gif_filename = f"{safe_case_name}_{timestamp}.gif"
                new_gif_path = os.path.join(gif_dir, new_gif_filename)

                # 移动并重命名文件
                shutil.move(default_gif_path, new_gif_path)

                # 保存相对路径到数据库（使用正斜杠，确保跨平台兼容）
                relative_path = f'media/ai_recording/{new_gif_filename}'
                execution_record.gif_path = relative_path

                logger.info(f"✅ GIF recording saved to: {relative_path}")
            else:
                logger.warning(f"⚠️ GIF file not found at: {default_gif_path}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to process GIF recording: {e}")

    def _auto_mark_completed_tasks(self, execution_record):
        """
        自动标记已完成的任务
        通过分析执行历史和当前任务状态，自动标记那些已经执行但未被标记完成的任务
        
        注意：已移除统一标记逻辑，任务状态完全由AI智能体通过mark_task_complete控制
        - 执行成功时标记为completed
        - 执行失败时标记为failed
        - 跳过执行时标记为skipped
        - 未执行时标记为pending
        """
        try:
            # 记录初始状态
            initial_completed = 0
            initial_pending = 0
            initial_failed = 0
            initial_skipped = 0
            
            if execution_record.planned_tasks:
                initial_completed = len([t for t in execution_record.planned_tasks if t.get('status') == 'completed'])
                initial_pending = len([t for t in execution_record.planned_tasks if t.get('status') == 'pending'])
                initial_failed = len([t for t in execution_record.planned_tasks if t.get('status') == 'failed'])
                initial_skipped = len([t for t in execution_record.planned_tasks if t.get('status') == 'skipped'])
                
                logger.info(f"📊 Task status summary: {initial_completed} completed, {initial_pending} pending, {initial_failed} failed, {initial_skipped} skipped")
            
            # 不再自动标记所有任务为完成
            # 任务状态完全由AI智能体通过mark_task_complete来控制
            logger.info("📋 Task statuses are controlled by AI agent via mark_task_complete action")

        except Exception as e:
            logger.warning(f"⚠️ Failed to summarize task statuses: {e}")


# 全局停止信号字典 {execution_id: bool}
STOP_SIGNALS = {}

TERMINAL_TASK_STATUSES = {'completed', 'failed', 'skipped'}
ACTIVE_TASK_STATUSES = {'pending', 'in_progress'}


def update_planned_task_status(planned_tasks, task_id, task_status):
    """更新子任务状态，返回是否命中任务。"""
    if not planned_tasks or task_id is None or not task_status:
        return False

    normalized_status = str(task_status).strip().lower()
    for task in planned_tasks:
        if str(task.get('id')) == str(task_id):
            task['status'] = normalized_status
            return True
    return False


def backfill_prior_pending_tasks(planned_tasks, current_task_id):
    """受限补齐：仅在强依赖场景下补齐紧邻前一步遗漏标记。"""
    if not planned_tasks or current_task_id is None:
        return []

    try:
        current_task_id_int = int(current_task_id)
    except (TypeError, ValueError):
        return []

    task_by_id = {}
    for task in planned_tasks:
        try:
            task_by_id[int(task.get('id'))] = task
        except (TypeError, ValueError):
            continue

    current_task = task_by_id.get(current_task_id_int)
    previous_task = task_by_id.get(current_task_id_int - 1)
    if not current_task or not previous_task:
        return []

    if previous_task.get('status', 'pending') not in ACTIVE_TASK_STATUSES:
        return []

    previous_desc = str(previous_task.get('description', '')).strip()
    current_desc = str(current_task.get('description', '')).strip()

    # 验证/检查类任务必须显式标记，禁止自动补齐
    verification_keywords = ['校验', '确认', '检查', '验证', '断言']
    if any(keyword in previous_desc for keyword in verification_keywords):
        return []

    dependency_pairs = [
        (['访问', '打开', '进入'], ['搜索', '输入', '点击', '查看']),
        (['搜索'], ['点击第', '点击第2条', '点击第二条', '查看详情']),
        (['点击第', '点击第2条', '点击第二条', '查看详情'], ['关闭', '关闭该标签页', '关闭标签页']),
        (['打开详情', '查看详情'], ['关闭', '返回']),
    ]

    def matches_any(text, keywords):
        return any(keyword in text for keyword in keywords)

    allowed = any(
        matches_any(previous_desc, prev_keywords) and matches_any(current_desc, curr_keywords)
        for prev_keywords, curr_keywords in dependency_pairs
    )

    if not allowed:
        return []

    previous_task['status'] = 'completed'
    return [current_task_id_int - 1]


def mark_first_active_task(planned_tasks, task_status):
    """在执行异常时为第一个未终态任务补一个状态。"""
    if not planned_tasks:
        return None

    normalized_status = str(task_status).strip().lower()
    for task in planned_tasks:
        if task.get('status', 'pending') in ACTIVE_TASK_STATUSES:
            task['status'] = normalized_status
            return task.get('id')
    return None


def summarize_planned_tasks(planned_tasks):
    """汇总子任务状态。"""
    summary = {
        'total': 0,
        'completed': 0,
        'failed': 0,
        'skipped': 0,
        'pending': 0,
        'in_progress': 0,
    }
    if not planned_tasks:
        return summary

    summary['total'] = len(planned_tasks)
    for task in planned_tasks:
        task_status = task.get('status', 'pending')
        if task_status in summary:
            summary[task_status] += 1
        else:
            summary['pending'] += 1
    return summary


def resolve_execution_status(planned_tasks):
    """根据子任务实际状态推导整单状态。"""
    summary = summarize_planned_tasks(planned_tasks)

    if summary['total'] == 0:
        return 'passed', summary
    if summary['failed'] > 0:
        return 'failed', summary
    if summary['pending'] > 0 or summary['in_progress'] > 0:
        return 'failed', summary
    return 'passed', summary


def append_execution_summary(logs, summary):
    """把任务统计附加到日志中。"""
    if summary['total'] == 0:
        return logs
    return (
        f"{logs}\n[System] 子任务统计: 总数 {summary['total']}，"
        f"已完成 {summary['completed']}，失败 {summary['failed']}，"
        f"跳过 {summary['skipped']}，待处理 {summary['pending'] + summary['in_progress']}。"
    )


def is_infrastructure_failure(error_message: str) -> bool:
    """判断是否为模型/网络/初始化类故障，这类问题不应直接把首个子任务标失败。"""
    message = (error_message or '').lower()
    infra_markers = [
        'execution llm unavailable',
        'connection error',
        'timed out',
        'timeout',
        'api key',
        'authentication',
        'unauthorized',
        'forbidden',
        'rate limit',
        'service unavailable',
    ]
    return any(marker in message for marker in infra_markers)


class AIExecutionRecordViewSet(viewsets.ModelViewSet):
    """AI执行记录视图集"""
    queryset = AIExecutionRecord.objects.all()
    serializer_class = AIExecutionRecordSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['project', 'ai_case', 'status']
    ordering = ['-start_time']

    def get_queryset(self):
        user = self.request.user
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        # 返回用户有权限的项目下的执行记录，以及没有关联项目的执行记录
        return AIExecutionRecord.objects.filter(
            models.Q(project__in=accessible_projects) | models.Q(project__isnull=True)
        ).distinct()

    def perform_destroy(self, instance):
        instance.delete()

    @action(detail=False, methods=['post'])
    def batch_delete(self, request):
        """批量删除AI执行记录"""
        try:
            ids = request.data.get('ids', [])

            # 验证ids参数
            if not ids:
                return Response({'error': '请选择要删除的记录'}, status=status.HTTP_400_BAD_REQUEST)

            # 确保ids是列表
            if not isinstance(ids, list):
                return Response({'error': 'ids参数格式错误，应为数组'}, status=status.HTTP_400_BAD_REQUEST)

            # 只能删除自己有权限的项目下的记录
            queryset = self.get_queryset()
            records_to_delete = queryset.filter(id__in=ids)

            # 检查是否有权限删除这些记录
            if not records_to_delete.exists():
                return Response({'error': '未找到可删除的记录或没有权限删除'}, status=status.HTTP_404_NOT_FOUND)

            # 获取可删除记录的ID列表，避免对distinct()后的queryset调用delete()
            deletable_ids = list(records_to_delete.values_list('id', flat=True))

            # 使用ID列表直接删除，避免distinct()的问题
            deleted_count = AIExecutionRecord.objects.filter(id__in=deletable_ids).delete()[0]

            return Response({'message': f'成功删除 {deleted_count} 条记录', 'deleted_count': deleted_count})
        except Exception as e:
            logger.error(f"批量删除AI执行记录失败: {str(e)}", exc_info=True)
            return Response({'error': f'批量删除失败: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='run_adhoc')
    def run_adhoc(self, request):
        """执行临时 AI 任务"""
        project_id = request.data.get('project_id')
        task_description = request.data.get('task_description')
        execution_mode = request.data.get('execution_mode', 'text')  # 默认文本模式
        enable_gif = request.data.get('enable_gif', True)  # GIF录制开关，默认开启

        if not task_description:
            return Response({'error': '缺少任务描述参数'}, status=status.HTTP_400_BAD_REQUEST)

        # 获取项目对象（如果提供了project_id）
        project = None
        if project_id:
            try:
                project = UiProject.objects.get(id=project_id)
            except UiProject.DoesNotExist:
                return Response({'error': '项目不存在'}, status=status.HTTP_404_NOT_FOUND)

        # 创建执行记录
        execution_record = AIExecutionRecord.objects.create(
            project=project,
            case_name="Adhoc Task",
            task_description=task_description,
            execution_mode=execution_mode,
            status='running',
            executed_by=request.user,
            logs="正在分析任务...\n"
        )

        # 异步执行
        import threading
        import os
        from asgiref.sync import sync_to_async
        from django.db import connection, DatabaseError
        from .ai_agent import run_full_process_sync

        def run_task():
            # 注册停止信号
            STOP_SIGNALS[execution_record.id] = False

            # 关键修复：关闭旧连接，避免子线程共享主线程的连接
            try:
                connection.close()
            except:
                pass

            # 设置环境变量，允许在后台线程中使用同步 ORM
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'

            def safe_save(record, update_fields=None, max_retries=3):
                """安全的保存方法，带有重试机制"""
                for attempt in range(max_retries):
                    try:
                        record.save(update_fields=update_fields)
                        return True
                    except (DatabaseError, Exception) as e:
                        error_str = str(e)
                        # 检查是否是MySQL连接错误
                        if '2006' in error_str or 'MySQL server has gone away' in error_str or '0' == error_str:
                            if attempt < max_retries - 1:
                                logger.warning(f"数据库连接失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                                # 关闭旧连接并重试
                                try:
                                    connection.close()
                                except:
                                    pass
                                import time
                                time.sleep(0.5)  # 等待一下再重试
                                continue
                            else:
                                logger.error(f"数据库保存失败，已达最大重试次数: {e}")
                                raise
                        else:
                            # 其他错误直接抛出
                            logger.error(f"数据库保存失败: {e}")
                            raise
                return False

            try:
                # 定义异步安全的 should_stop
                async def should_stop_async():
                    # 优先检查内存信号
                    if STOP_SIGNALS.get(execution_record.id, False):
                        return True
                    # 兜底检查数据库状态 (使用 sync_to_async 避免异步上下文错误)
                    await sync_to_async(execution_record.refresh_from_db)()
                    return execution_record.status == 'stopped'

                # 定义同步版本的 should_stop 用于最后检查
                def should_stop_sync():
                    if STOP_SIGNALS.get(execution_record.id, False):
                        return True
                    execution_record.refresh_from_db()
                    return execution_record.status == 'stopped'

                async def on_analysis_complete(planned_tasks):
                    execution_record.planned_tasks = planned_tasks
                    execution_record.logs += "任务分析完成，开始执行...\n"
                    await sync_to_async(safe_save)(execution_record, update_fields=['planned_tasks', 'logs'])

                async def on_step_update(step_info):
                    try:
                        # 处理日志
                        if step_info.get('type') == 'log':
                            content = step_info.get('content')
                            if content:
                                execution_record.logs += content
                                # 立即保存到数据库，确保前端轮询能看到最新日志
                                await sync_to_async(safe_save)(execution_record, update_fields=['logs'])
                            return

                        # 处理任务状态
                        task_id = step_info.get('task_id')
                        status = step_info.get('status')
                        logger.info(f"DEBUG: on_step_update received: task_id={task_id}, status={status}")

                        if task_id and status:
                            updated = False
                            if execution_record.planned_tasks:
                                old_status = None
                                for task in execution_record.planned_tasks:
                                    if str(task.get('id')) == str(task_id):
                                        old_status = task.get('status', 'pending')
                                        break
                                backfilled_ids = []
                                if str(status).strip().lower() == 'completed':
                                    backfilled_ids = backfill_prior_pending_tasks(
                                        execution_record.planned_tasks,
                                        task_id
                                    )
                                    if backfilled_ids:
                                        execution_record.logs += (
                                            f"\n[System] 已补齐遗漏标记的前序子任务: "
                                            f"{', '.join(map(str, backfilled_ids))}"
                                        )
                                updated = update_planned_task_status(
                                    execution_record.planned_tasks,
                                    task_id,
                                    status
                                )
                                if updated:
                                    logger.info(f"DEBUG: Updated task {task_id} from {old_status} to {status}")
                            if updated:
                                # 立即保存到数据库，确保前端轮询能看到最新状态
                                update_fields = ['planned_tasks']
                                if 'backfilled_ids' in locals() and backfilled_ids:
                                    update_fields.append('logs')
                                await sync_to_async(safe_save)(execution_record, update_fields=update_fields)
                            else:
                                logger.warning(
                                    f"DEBUG: Task ID {task_id} not found in planned_tasks: {execution_record.planned_tasks}")
                    except Exception as e:
                        logger.error(f"更新步骤状态失败: {e}", exc_info=True)

                history = run_full_process_sync(
                    task_description,
                    analysis_callback=on_analysis_complete,
                    step_callback=on_step_update,
                    should_stop=should_stop_async,  # 传递异步版本
                    execution_mode=execution_mode,
                    enable_gif=enable_gif,  # 传递GIF录制开关
                    case_name=task_description[:50] if task_description else "Adhoc Task"  # 传递用例名称用于GIF文件命名
                )

                # 检查是否是手动停止 (使用同步版本)
                if should_stop_sync():
                    execution_record.status = 'stopped'
                    execution_record.logs += "\n[System] 任务已由用户停止。"
                else:
                    # 根据执行结果判定失败
                    failed = False
                    if execution_record.planned_tasks:
                        failed = any(t.get('status') in ('failed', 'error') for t in execution_record.planned_tasks)
                    if not failed and history:
                        try:
                            steps = history.steps if hasattr(history, 'steps') else []
                            for step in steps:
                                if getattr(step, 'status', None) == 'failed':
                                    failed = True
                                    break
                        except Exception:
                            pass

                    if failed:
                        execution_record.status = 'failed'
                        execution_record.logs += "\n执行完成，但检测到失败步骤或任务。"
                    else:
                        execution_record.status = 'passed'
                        execution_record.logs += "\n执行完成。"

                    # 记录任务完成统计信息
                    if execution_record.planned_tasks:
                        total_tasks = len(execution_record.planned_tasks)
                        completed_tasks = len(
                            [t for t in execution_record.planned_tasks if t.get('status') == 'completed'])
                        pending_tasks = len([t for t in execution_record.planned_tasks if t.get('status') == 'pending'])
                        logger.info(
                            f"🏁 Task completion summary: {completed_tasks}/{total_tasks} tasks completed, {pending_tasks} pending")

                execution_record.end_time = timezone.now()
                execution_record.duration = (execution_record.end_time - execution_record.start_time).total_seconds()

                # 格式化 history 为日志 (如果不是停止状态)
                steps = []
                if history:
                    if hasattr(history, 'steps'):
                        steps = [extract_step_info(s, i) for i, s in enumerate(history.steps)]

                execution_record.steps_completed = steps

                # 自动标记已完成的任务
                if execution_record.planned_tasks:
                    self._auto_mark_completed_tasks(execution_record)
                    execution_record.logs = append_execution_summary(
                        execution_record.logs,
                        summarize_planned_tasks(execution_record.planned_tasks)
                    )

                # 处理GIF录制文件
                self._process_gif_recording(execution_record, history)

                safe_save(execution_record)

            except Exception as e:
                error_message = str(e)
                failed_task_id = None if is_infrastructure_failure(error_message) else mark_first_active_task(execution_record.planned_tasks, 'failed')
                execution_record.status = 'failed'
                execution_record.end_time = timezone.now()
                execution_record.duration = (execution_record.end_time - execution_record.start_time).total_seconds()
                if 'Execution LLM unavailable' in error_message:
                    execution_record.logs += f"\n执行出错: AI 执行模型连接失败。{error_message}"
                else:
                    execution_record.logs += f"\n执行出错: {error_message}"
                if failed_task_id is not None:
                    execution_record.logs += f"\n[System] 子任务 {failed_task_id} 已自动标记为失败。"
                execution_record.logs = append_execution_summary(
                    execution_record.logs,
                    summarize_planned_tasks(execution_record.planned_tasks)
                )
                try:
                    safe_save(execution_record)
                except:
                    # 如果保存失败，至少尝试保存基本信息
                    logger.error(f"保存失败状态时出错: {e}")
                    pass
            finally:
                # 清理停止信号
                if execution_record.id in STOP_SIGNALS:
                    del STOP_SIGNALS[execution_record.id]

        thread = threading.Thread(target=run_task)
        thread.daemon = True
        thread.start()

        return Response({
            'message': 'AI 任务开始执行',
            'execution_id': execution_record.id
        })

    @action(detail=True, methods=['post'], url_path='stop')
    def stop_task(self, request, pk=None):
        """停止正在执行的任务"""
        try:
            execution_id = int(pk)
            if execution_id in STOP_SIGNALS:
                STOP_SIGNALS[execution_id] = True
                return Response({'message': '已发送停止信号'})
            else:
                # 如果不在内存中，可能已经结束，或者重启过服务
                # 尝试直接更新数据库状态
                record = self.get_object()
                if record.status == 'running':
                    record.status = 'stopped'
                    record.end_time = timezone.now()
                    record.logs += "\n[System] 任务被强制标记为停止（未在运行队列中找到）。"
                    record.save()
                    return Response({'message': '任务已标记为停止'})
                return Response({'message': '任务不在运行中'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _process_gif_recording(self, execution_record, history):
        """
        处理GIF录制文件
        在执行完成后查找生成的GIF文件并保存路径到数据库
        """
        try:
            import os
            from django.conf import settings
            from datetime import datetime

            # browser-use 默认生成的GIF文件名（固定为agent_history.gif）
            default_gif_path = os.path.join(os.getcwd(), 'agent_history.gif')

            # 如果找到GIF文件，移动到media/ai_recording目录并重命名
            if os.path.exists(default_gif_path):
                import shutil

                # 创建录制文件目录
                gif_dir = os.path.join(settings.MEDIA_ROOT, 'ai_recording')
                os.makedirs(gif_dir, exist_ok=True)

                # 生成新的文件名：用例名称+年月日时分秒
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                # 清理用例名称中的非法字符
                safe_case_name = "".join(
                    [c if c.isalnum() or c in (' ', '_', '-') else '_' for c in execution_record.case_name])
                new_gif_filename = f"{safe_case_name}_{timestamp}.gif"
                new_gif_path = os.path.join(gif_dir, new_gif_filename)

                # 移动并重命名文件
                shutil.move(default_gif_path, new_gif_path)

                # 保存相对路径到数据库（使用正斜杠，确保跨平台兼容）
                relative_path = f'media/ai_recording/{new_gif_filename}'
                execution_record.gif_path = relative_path

                logger.info(f"✅ GIF recording saved to: {relative_path}")
            else:
                logger.warning(f"⚠️ GIF file not found at: {default_gif_path}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to process GIF recording: {e}")

    def _auto_mark_completed_tasks(self, execution_record):
        """
        自动标记已完成的任务
        通过分析执行历史和当前任务状态，自动标记那些已经执行但未被标记完成的任务
        
        注意：已移除统一标记逻辑，任务状态完全由AI智能体通过mark_task_complete控制
        - 执行成功时标记为completed
        - 执行失败时标记为failed
        - 跳过执行时标记为skipped
        - 未执行时标记为pending
        """
        try:
            # 记录初始状态
            initial_completed = 0
            initial_pending = 0
            initial_failed = 0
            initial_skipped = 0
            
            if execution_record.planned_tasks:
                initial_completed = len([t for t in execution_record.planned_tasks if t.get('status') == 'completed'])
                initial_pending = len([t for t in execution_record.planned_tasks if t.get('status') == 'pending'])
                initial_failed = len([t for t in execution_record.planned_tasks if t.get('status') == 'failed'])
                initial_skipped = len([t for t in execution_record.planned_tasks if t.get('status') == 'skipped'])
                
                logger.info(f"📊 Task status summary: {initial_completed} completed, {initial_pending} pending, {initial_failed} failed, {initial_skipped} skipped")
            
            # 不再自动标记所有任务为完成
            # 任务状态完全由AI智能体通过mark_task_complete来控制
            logger.info("📋 Task statuses are controlled by AI agent via mark_task_complete action")

        except Exception as e:
            logger.warning(f"⚠️ Failed to summarize task statuses: {e}")

    @action(detail=True, methods=['get'], url_path='report')
    def generate_report(self, request, pk=None):
        """
        生成AI执行报告

        Query Parameters:
            report_type: 报告类型 (summary/detailed/performance)，默认为 summary

        Returns:
            执行报告数据
        """
        try:
            record = self.get_object()
            report_type = request.query_params.get('report_type', 'summary')

            # 导入报告生成器
            from .reports import AIExecutionReportGenerator

            # 生成报告
            generator = AIExecutionReportGenerator(record)

            if report_type == 'detailed':
                report = generator.generate_detailed_report()
            elif report_type == 'performance':
                report = generator.generate_performance_report()
            else:  # summary
                report = generator.generate_summary_report()

            return Response({
                'success': True,
                'data': report,
                'report_type': report_type
            })

        except Exception as e:
            logger.error(f"生成AI执行报告失败: {e}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='export-pdf')
    def export_pdf(self, request, pk=None):
        """
        导出AI执行报告为PDF

        Query Parameters:
            report_type: 报告类型 (summary/detailed/performance)，默认为 summary

        Returns:
            PDF文件下载
        """
        try:
            record = self.get_object()
            report_type = request.query_params.get('report_type', 'summary')

            # 导入报告生成器
            from .reports import AIExecutionReportGenerator
            from .pdf_generator import AIReportPDFGenerator

            # 生成报告数据
            generator = AIExecutionReportGenerator(record)

            if report_type == 'detailed':
                report_data = generator.generate_detailed_report()
            elif report_type == 'performance':
                report_data = generator.generate_performance_report()
            else:  # summary
                report_data = generator.generate_summary_report()

            # 生成PDF
            pdf_generator = AIReportPDFGenerator(report_data, report_type)
            pdf_buffer = pdf_generator.generate()

            # 生成文件名
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            safe_case_name = "".join([c if c.isalnum() or c in (' ', '_', '-') else '_' for c in record.case_name])
            filename = f"AI_Report_{safe_case_name}_{timestamp}.pdf"

            # 返回PDF文件
            response = HttpResponse(
                pdf_buffer.getvalue(),
                content_type='application/pdf'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(pdf_buffer.getvalue())

            return response

        except ImportError as e:
            logger.error(f"PDF生成库未安装: {e}")
            return Response({
                'success': False,
                'error': 'PDF生成功能需要安装 reportlab 库，请运行: pip install reportlab'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logger.error(f"导出PDF失败: {e}", exc_info=True)
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UiDashboardViewSet(viewsets.ViewSet):
    """UI自动化仪表盘视图集"""
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """获取仪表盘统计数据"""
        user = request.user

        # 获取用户可访问的项目ID列表
        accessible_projects = UiProject.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()
        project_ids = accessible_projects.values_list('id', flat=True)

        # 统计数据
        project_count = accessible_projects.count()

        # 测试用例数量
        test_case_count = TestCase.objects.filter(project_id__in=project_ids).count()

        # 测试套件数量（包含用例总数）
        suite_count = TestSuite.objects.filter(project_id__in=project_ids).count()

        from .models import TestSuiteTestCase
        suite_test_case_count = TestSuiteTestCase.objects.filter(
            test_suite__project_id__in=project_ids
        ).count()

        # 测试执行数量（传统+新版）
        execution_count = TestExecution.objects.filter(project_id__in=project_ids).count()
        test_case_execution_count = TestCaseExecution.objects.filter(project_id__in=project_ids).count()
        total_execution_count = execution_count + test_case_execution_count

        return Response({
            'project_count': project_count,
            'test_case_count': test_case_count,
            'suite_count': suite_test_case_count,
            'execution_count': total_execution_count
        })
