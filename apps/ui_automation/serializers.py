from rest_framework import serializers
from django.db.models import Q
from django.utils import timezone
from .models import (
    UiProject, StorageState, LocatorStrategy, Element, TestScript, TestSuite,
    TestSuiteScript, TestSuiteTestCase, TestExecution, TestEnvironment, Screenshot,
    ElementGroup, PageObject, PageObjectElement, ScriptStep, ScriptElementUsage,
    TestCase, TestCaseFolder, TestCaseStep, TestCaseExecution, OperationRecord,
    UiScheduledTask, UiNotificationLog, UiTaskNotificationSetting,
    AICase, AIExecutionRecord
)
from django.contrib.auth import get_user_model
from urllib.parse import urlparse
import re
import json

User = get_user_model()


def _element_case_step_count(obj):
    """返回正式 UI 用例中引用该元素的步骤条数。"""
    if hasattr(obj, 'case_step_count'):
        return obj.case_step_count
    return TestCaseStep.objects.filter(element_id=obj.pk).count()


def resolve_page_group(project, page, current_group=None):
    """按页面名称复用任意层级分组，并为业务模块页面推断父分组。"""
    page_name = str(page or '').strip()
    if not page_name:
        return None

    if (
        current_group is not None
        and current_group.project_id == project.id
        and current_group.name.casefold() == page_name.casefold()
    ):
        return current_group

    groups = ElementGroup.objects.filter(project=project).select_related('parent_group').order_by('id')
    exact_group = groups.filter(name=page_name).first()
    if exact_group is not None and exact_group.parent_group_id is not None:
        return exact_group
    roots = [group for group in groups if group.parent_group_id is None]

    # 新录制的“Post Create”或“Post-Settings”页面自动归入 Post 模块。
    # 仅匹配带分隔符的前缀，避免把“Postcard”误判为 Post 子页面。
    page_folded = page_name.casefold()
    parent = None
    for candidate in sorted(roots, key=lambda group: len(group.name), reverse=True):
        prefix = candidate.name.strip()
        prefix_folded = prefix.casefold()
        if prefix and page_folded.startswith(prefix_folded) and len(page_folded) > len(prefix_folded):
            separator = page_name[len(prefix):len(prefix) + 1]
            if separator in {' ', '/', '-', '_'}:
                parent = candidate
                break

    if parent is None and exact_group is not None:
        return exact_group

    if parent is None:
        return ElementGroup.objects.create(
            project=project,
            name=page_name,
            parent_group=None,
            description='由元素页面自动创建',
        )

    child_name = page_name[len(parent.name):].lstrip(' /-_').strip()
    if not child_name:
        return exact_group or parent
    existing_child = ElementGroup.objects.filter(
        project=project,
        parent_group=parent,
        name__iexact=child_name,
    ).order_by('id').first()
    if existing_child is not None:
        return existing_child

    # Store only the module-local page name so it matches the element tree.
    child_name = child_name[:1].upper() + child_name[1:]
    return ElementGroup.objects.create(
        project=project,
        name=child_name,
        parent_group=parent,
        description='由元素页面自动创建',
    )


def validate_test_case_step_data(
    *,
    action_type,
    assert_type='',
    input_value='',
    assert_value='',
    element=None,
    project_id=None,
    action_params=None,
):
    """校验 UI 测试步骤的动作、目标元素和参数之间的约束。"""
    element_actions = {
        'click', 'fill', 'press', 'select', 'check', 'uncheck',
        'getText', 'waitFor', 'hover', 'scroll',
    }

    if action_type == 'navigate':
        url = str(input_value or '').strip()
        parsed_url = urlparse(url)
        # 支持运行时变量，例如 {base_url}/login，由执行器在运行时解析。
        has_runtime_variable = bool(re.search(r'\{[^{}]+\}', url))
        if not url or (
            not has_runtime_variable
            and (parsed_url.scheme not in {'http', 'https'} or not parsed_url.netloc)
        ):
            raise serializers.ValidationError('请输入有效的http/https URL')
        if element:
            raise serializers.ValidationError('跳转URL步骤不能绑定元素')
    elif action_type == 'assert' and assert_type == 'urlContains':
        if not str(assert_value or '').strip():
            raise serializers.ValidationError('URL包含断言需要填写断言值')
        if element:
            raise serializers.ValidationError('URL包含断言不能绑定元素')
    elif action_type in element_actions or action_type == 'assert':
        if not element:
            raise serializers.ValidationError('该步骤类型需要选择目标元素')

    if element and project_id is not None and str(element.project_id) != str(project_id):
        raise serializers.ValidationError('目标元素必须属于当前项目')

    # 登录态步骤使用独立参数校验，保存和切换必须引用合法的登录态资源。
    if action_type == 'storageState':
        params = action_params or {}
        operation = params.get('operation')
        if operation not in {'save', 'switch', 'clear'}:
            raise serializers.ValidationError('登录态动作必须选择保存、切换或清除')
        if operation == 'save':
            if not str(params.get('name') or '').strip():
                raise serializers.ValidationError('保存登录态需要填写名称')
            if params.get('mode', 'save_as') not in {'overwrite', 'save_as'}:
                raise serializers.ValidationError('登录态保存方式无效')
        elif operation == 'switch' and not params.get('storage_state_id'):
            raise serializers.ValidationError('切换登录态需要选择目标登录态')


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email')


class UiProjectSerializer(serializers.ModelSerializer):
    owner = UserSerializer(read_only=True)
    members = UserSerializer(many=True, read_only=True)

    class Meta:
        model = UiProject
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'workspace_project', 'is_deleted', 'deleted_at', 'deleted_by')


class UiProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UiProject
        fields = ('name', 'description', 'status', 'base_url', 'start_date', 'end_date', 'owner', 'members', 'variables')

    def validate_variables(self, value):
        from .variable_context import VariableError, validate_variables
        try:
            return validate_variables(value)
        except VariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class UiProjectUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UiProject
        fields = ('name', 'description', 'status', 'base_url', 'start_date', 'end_date', 'members', 'variables')

    def validate_variables(self, value):
        from .variable_context import VariableError, validate_variables
        try:
            return validate_variables(value)
        except VariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class StorageStateSerializer(serializers.ModelSerializer):
    """校验并序列化项目级 storage_state 文件。"""
    project_id = serializers.IntegerField(write_only=True, required=False)
    content = serializers.JSONField(write_only=True, required=False)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = StorageState
        fields = ('id', 'project', 'project_id', 'name', 'source_url', 'browser', 'file', 'content', 'file_size',
                  'download_url', 'created_by', 'created_at', 'updated_at')
        read_only_fields = ('project', 'file_size', 'download_url', 'created_by', 'created_at', 'updated_at')
        extra_kwargs = {'file': {'required': False}}

    def get_download_url(self, obj):
        request = self.context.get('request')
        if not obj.file:
            return ''
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url

    def validate_name(self, value):
        value = str(value or '').strip()
        if not value:
            raise serializers.ValidationError('请输入登录态名称')
        return value[:200]

    def validate(self, attrs):
        uploaded = attrs.get('file')
        content = attrs.get('content')
        if content is not None and (
            not isinstance(content, dict)
            or not isinstance(content.get('cookies', []), list)
            or not isinstance(content.get('origins', []), list)
        ):
            raise serializers.ValidationError({'content': '登录态 JSON 必须包含 cookies 和 origins 数组'})
        if not self.instance and not uploaded and content is None:
            raise serializers.ValidationError({'file': '请上传登录态文件或提供 content 内容'})
        if uploaded:
            if uploaded.size > 2 * 1024 * 1024:
                raise serializers.ValidationError({'file': '登录态文件不能超过2MB'})
            try:
                payload = json.loads(uploaded.read().decode('utf-8'))
                uploaded.seek(0)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise serializers.ValidationError({'file': '登录态文件必须是有效的UTF-8 JSON'}) from exc
            if not isinstance(payload, dict) or not isinstance(payload.get('cookies', []), list) or not isinstance(payload.get('origins', []), list):
                raise serializers.ValidationError({'file': '登录态 JSON 必须包含 cookies 和 origins 数组'})
            attrs['file_size'] = uploaded.size
        return attrs

    def create(self, validated_data):
        content = validated_data.pop('content', None)
        if content is None:
            return super().create(validated_data)
        instance = StorageState(**validated_data)
        instance.write_content(json.dumps(content, ensure_ascii=False, indent=2).encode('utf-8'))
        return instance


class LocatorStrategySerializer(serializers.ModelSerializer):
    class Meta:
        model = LocatorStrategy
        fields = '__all__'


class ElementSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    locator_strategy = LocatorStrategySerializer(read_only=True)
    created_by = UserSerializer(read_only=True)
    usage_count = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(write_only=True)
    group_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    locator_strategy_id = serializers.IntegerField()  # 显式定义，支持读写

    class Meta:
        model = Element
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'created_by')

    def get_usage_count(self, obj):
        return _element_case_step_count(obj)

    def validate_project_id(self, value):
        """验证项目ID是否有效"""
        try:
            UiProject.objects.get(id=value)
        except UiProject.DoesNotExist:
            raise serializers.ValidationError("请选择有效的项目")

        # 更新项目时，校验现有分组是否仍属于目标项目。
        group_id = self.initial_data.get('group_id', getattr(self.instance, 'group_id', None))
        if group_id:
            group = ElementGroup.objects.filter(id=group_id).first()
            if group and str(group.project_id) != str(value):
                raise serializers.ValidationError("元素分组必须属于当前项目")
        return value

    def validate_locator_strategy_id(self, value):
        """验证定位策略ID是否有效"""
        if value is not None:
            try:
                LocatorStrategy.objects.get(id=value)
            except LocatorStrategy.DoesNotExist:
                raise serializers.ValidationError("请选择有效的定位策略")
        return value

    def validate_group_id(self, value):
        """验证分组ID是否有效"""
        if value is not None:  # 允许None值
            try:
                group = ElementGroup.objects.get(id=value)
            except ElementGroup.DoesNotExist:
                raise serializers.ValidationError("请选择有效的元素分组")

            # 创建或移动元素时，禁止关联其他项目下的分组。
            project_id = self.initial_data.get('project_id', getattr(self.instance, 'project_id', None))
            if project_id is not None and str(group.project_id) != str(project_id):
                raise serializers.ValidationError("元素分组必须属于当前项目")
        return value

    @staticmethod
    def _resolve_page_group(project, page, current_group=None):
        return resolve_page_group(project, page, current_group=current_group)

    def create(self, validated_data):
        # 处理外键字段
        project_id = validated_data.pop('project_id')
        locator_strategy_id = validated_data.pop('locator_strategy_id', None)
        group_id = validated_data.pop('group_id', None)
        group_field_present = 'group_id' in self.initial_data

        validated_data['project'] = UiProject.objects.get(id=project_id)

        if locator_strategy_id:
            validated_data['locator_strategy'] = LocatorStrategy.objects.get(id=locator_strategy_id)

        if group_field_present:
            # 显式传入分组时尊重调用方，包括传 null 清除分组。
            validated_data['group'] = (
                ElementGroup.objects.get(id=group_id) if group_id else None
            )
        elif validated_data.get('page'):
            # API 调用方通常只提交 page；保持页面字段与元素树分组一致。
            group = self._resolve_page_group(
                validated_data['project'], validated_data['page']
            )
            validated_data['group'] = group
            if group:
                validated_data['page'] = group.name

        return super().create(validated_data)

    def update(self, instance, validated_data):
        # 处理外键字段
        project_id = validated_data.pop('project_id', None)
        locator_strategy_id = validated_data.pop('locator_strategy_id', None)
        group_id = validated_data.pop('group_id', None)
        group_field_present = 'group_id' in self.initial_data

        if project_id:
            validated_data['project'] = UiProject.objects.get(id=project_id)

        if locator_strategy_id:
            validated_data['locator_strategy'] = LocatorStrategy.objects.get(id=locator_strategy_id)

        if group_field_present:  # 允许设置为None来清除分组
            validated_data['group'] = (
                ElementGroup.objects.get(id=group_id) if group_id else None
            )
        elif not group_field_present and 'page' in validated_data:
            # 修改页面时未显式传 group_id，自动移动到对应页面分组。
            group = self._resolve_page_group(
                validated_data.get('project', instance.project),
                validated_data.get('page'),
                current_group=instance.group,
            )
            validated_data['group'] = group
            if group:
                validated_data['page'] = group.name

        return super().update(instance, validated_data)


class TestScriptSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = TestScript
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class TestScriptCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestScript
        fields = ('project', 'name', 'description', 'script_type', 'content', 'language', 'framework',
                  'source', 'start_url', 'recording_config', 'analysis')


class TestScriptUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestScript
        fields = ('name', 'description', 'script_type', 'content', 'start_url', 'recording_config')


class TestSuiteScriptSerializer(serializers.ModelSerializer):
    test_script = TestScriptSerializer(read_only=True)
    test_script_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = TestSuiteScript
        fields = ('id', 'test_script', 'test_script_id', 'order')


class TestSuiteTestCaseSerializer(serializers.ModelSerializer):
    test_case = serializers.SerializerMethodField()
    test_case_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = TestSuiteTestCase
        fields = ('id', 'test_case', 'test_case_id', 'order')

    def get_test_case(self, obj):
        """获取测试用例信息"""
        test_case = obj.test_case
        return {
            'id': test_case.id,
            'name': test_case.name,
            'description': test_case.description,
            'status': test_case.status,
            'priority': test_case.priority,
            'created_at': test_case.created_at
        }


class TestSuiteSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    scripts = TestScriptSerializer(many=True, read_only=True)
    test_cases_data = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(write_only=True)
    suite_scripts = TestSuiteScriptSerializer(many=True, read_only=True)
    suite_test_cases = TestSuiteTestCaseSerializer(many=True, read_only=True)
    test_case_count = serializers.SerializerMethodField()

    class Meta:
        model = TestSuite
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'execution_status', 'passed_count', 'failed_count')

    def get_test_cases_data(self, obj):
        """获取测试用例数据"""
        return TestSuiteTestCaseSerializer(obj.suite_test_cases.all(), many=True).data

    def get_test_case_count(self, obj):
        """获取测试用例数量"""
        return obj.suite_test_cases.count()


class TestSuiteListSerializer(serializers.ModelSerializer):
    """套件列表只返回摘要与用例数，不展开项目、脚本或用例关联。"""
    project_name = serializers.CharField(source='project.name', read_only=True)
    test_case_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TestSuite
        fields = (
            'id', 'project', 'project_name', 'name', 'description', 'execution_status',
            'passed_count', 'failed_count', 'test_case_count', 'created_at', 'updated_at',
        )


class TestSuiteCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestSuite
        fields = ('id', 'project', 'name', 'description')
        read_only_fields = ('id',)


class TestSuiteUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestSuite
        fields = ('name', 'description')


class TestSuiteWithScriptsSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    suite_scripts = TestSuiteScriptSerializer(many=True, read_only=True)

    class Meta:
        model = TestSuite
        fields = '__all__'


class TestExecutionSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    test_suite = TestSuiteSerializer(read_only=True)
    test_script = TestScriptSerializer(read_only=True)
    executed_by = UserSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True)
    test_suite_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    test_script_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    executed_by_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    # 添加计算字段
    test_suite_name = serializers.SerializerMethodField()
    executed_by_name = serializers.SerializerMethodField()
    pass_rate = serializers.SerializerMethodField()

    class Meta:
        model = TestExecution
        fields = '__all__'
        read_only_fields = (
            'created_at', 'started_at', 'finished_at', 'duration',
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases'
        )

    def get_test_suite_name(self, obj):
        """获取测试套件名称"""
        return obj.test_suite.name if obj.test_suite else '-'
    
    def get_executed_by_name(self, obj):
        """获取执行人姓名"""
        return obj.executed_by.username if obj.executed_by else '-'

    def get_pass_rate(self, obj):
        """获取通过率"""
        return obj.pass_rate


class TestExecutionListSerializer(serializers.ModelSerializer):
    """执行列表仅返回状态、统计和关联名称，大字段由详情与日志接口按需读取。"""
    project_name = serializers.CharField(source='project.name', read_only=True)
    test_suite_name = serializers.CharField(source='test_suite.name', default='-', read_only=True)
    test_script_name = serializers.CharField(source='test_script.name', default='-', read_only=True)
    executed_by_name = serializers.CharField(source='executed_by.username', default='-', read_only=True)
    pass_rate = serializers.ReadOnlyField()

    class Meta:
        model = TestExecution
        fields = (
            'id', 'project', 'project_name', 'test_suite', 'test_suite_name',
            'test_script', 'test_script_name', 'executed_by', 'executed_by_name',
            'environment', 'status', 'engine', 'browser', 'headless', 'execution_type',
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases', 'pass_rate',
            'started_at', 'finished_at', 'duration', 'report_url', 'created_at',
        )


class TestExecutionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestExecution
        fields = ('project', 'test_suite', 'test_script', 'environment', 'executed_by')


class ScreenshotSerializer(serializers.ModelSerializer):
    execution = TestExecutionSerializer(read_only=True)
    execution_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = Screenshot
        fields = '__all__'
        read_only_fields = ('created_at', 'captured_at')


class ElementGroupProjectSerializer(UiProjectSerializer):
    """在一次分组响应内复用项目数据，避免每个节点重复序列化同一项目。"""

    def to_representation(self, instance):
        project_cache = self.context.get('element_group_projects')
        if project_cache is None:
            return super().to_representation(instance)
        if instance.pk not in project_cache:
            project_cache[instance.pk] = super().to_representation(instance)
        return project_cache[instance.pk]


# 新增的serializers
class ElementGroupSerializer(serializers.ModelSerializer):
    project = ElementGroupProjectSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True)
    elements_count = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()

    class Meta:
        model = ElementGroup
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

    def get_elements_count(self, obj):
        """获取分组下的元素数量"""
        if hasattr(obj, 'direct_elements_count'):
            return obj.direct_elements_count
        return obj.elements.count()

    def get_children(self, obj):
        """获取子分组"""
        children_by_parent = self.context.get('element_group_children')
        if children_by_parent is not None:
            children = children_by_parent.get(obj.pk, [])
            return ElementGroupSerializer(children, many=True, context=self.context).data

        children = obj.elementgroup_set.all()
        return ElementGroupSerializer(children, many=True, context=self.context).data


class ElementGroupCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ElementGroup
        fields = ('project', 'name', 'description', 'parent_group', 'order')


class ElementEnhancedSerializer(serializers.ModelSerializer):
    """增强的元素序列化器，包含新字段"""
    project = UiProjectSerializer(read_only=True)
    group = ElementGroupSerializer(read_only=True)
    locator_strategy = LocatorStrategySerializer(read_only=True)
    created_by = UserSerializer(read_only=True)
    parent_element = serializers.SerializerMethodField()
    children_elements = serializers.SerializerMethodField()
    all_locators = serializers.SerializerMethodField()
    usage_scripts = serializers.SerializerMethodField()
    usage_count = serializers.SerializerMethodField()

    # Write-only fields for foreign keys
    project_id = serializers.IntegerField(write_only=True)
    group_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    locator_strategy_id = serializers.IntegerField()  # 允许读写,支持回显
    parent_element_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Element
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'created_by', 'usage_count', 'last_validated')

    def get_parent_element(self, obj):
        """获取父元素信息"""
        if obj.parent_element:
            return {
                'id': obj.parent_element.id,
                'name': obj.parent_element.name,
                'page': obj.parent_element.page
            }
        return None

    def get_usage_count(self, obj):
        return _element_case_step_count(obj)

    def get_children_elements(self, obj):
        """获取子元素"""
        children = obj.element_set.all()
        return [{
            'id': child.id,
            'name': child.name,
            'element_type': child.element_type
        } for child in children]

    def get_all_locators(self, obj):
        """获取所有定位器（主要+备用）"""
        return obj.get_all_locators()

    def get_usage_scripts(self, obj):
        """获取使用此元素的脚本列表"""
        usages = obj.script_usages.select_related('script').all()[:5]  # 只返回前5个
        return [{
            'script_id': usage.script.id,
            'script_name': usage.script.name,
            'usage_type': usage.usage_type,
            'frequency': usage.frequency
        } for usage in usages]

    def create(self, validated_data):
        # 处理外键字段
        project_id = validated_data.pop('project_id')
        locator_strategy_id = validated_data.pop('locator_strategy_id')
        group_id = validated_data.pop('group_id', None)
        parent_element_id = validated_data.pop('parent_element_id', None)

        validated_data['project'] = UiProject.objects.get(id=project_id)
        validated_data['locator_strategy'] = LocatorStrategy.objects.get(id=locator_strategy_id)

        if group_id:
            validated_data['group'] = ElementGroup.objects.get(id=group_id)

        if parent_element_id:
            validated_data['parent_element'] = Element.objects.get(id=parent_element_id)

        return super().create(validated_data)

    def update(self, instance, validated_data):
        # 处理外键字段
        project_id = validated_data.pop('project_id', None)
        locator_strategy_id = validated_data.pop('locator_strategy_id', None)
        group_id = validated_data.pop('group_id', None)
        parent_element_id = validated_data.pop('parent_element_id', None)

        if project_id:
            validated_data['project'] = UiProject.objects.get(id=project_id)

        if locator_strategy_id:
            validated_data['locator_strategy'] = LocatorStrategy.objects.get(id=locator_strategy_id)

        if group_id is not None:  # 允许设置为None来清除分组
            if group_id:
                validated_data['group'] = ElementGroup.objects.get(id=group_id)
            else:
                validated_data['group'] = None

        if parent_element_id is not None:  # 允许设置为None来清除父元素
            if parent_element_id:
                validated_data['parent_element'] = Element.objects.get(id=parent_element_id)
            else:
                validated_data['parent_element'] = None

        return super().update(instance, validated_data)


class ElementListSerializer(serializers.ModelSerializer):
    """元素列表和选择器共用扁平字段，不加载项目、分组及引用详情。"""
    locator_strategy = serializers.CharField(source='locator_strategy.name', read_only=True)

    class Meta:
        model = Element
        fields = (
            'id', 'name', 'element_type', 'locator_strategy', 'locator_value',
            'locator_options', 'page', 'group_id',
            'description', 'created_at', 'updated_at',
        )


class ElementTreeSerializer(ElementListSerializer):
    """树节点只保留展示和定位所需字段，引用次数由详情接口提供。"""
    type = serializers.ReadOnlyField(default='element')
    children = serializers.ListField(read_only=True, default=list)

    class Meta(ElementListSerializer.Meta):
        fields = (
            'id', 'name', 'element_type', 'locator_strategy', 'locator_value',
            'locator_options', 'page', 'group_id', 'validation_status', 'order',
            'type', 'children',
        )


class PageObjectSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    created_by = UserSerializer(read_only=True)
    elements_count = serializers.SerializerMethodField()
    elements = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = PageObject
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'created_by', 'template_code')

    def get_elements_count(self, obj):
        """获取页面对象包含的元素数量"""
        return obj.page_object_elements.count()

    def get_elements(self, obj):
        """获取页面对象包含的元素"""
        po_elements = obj.page_object_elements.select_related('element').all()
        return [{
            'id': po_element.id,
            'element_id': po_element.element.id,
            'element_name': po_element.element.name,
            'method_name': po_element.method_name,
            'is_property': po_element.is_property,
            'order': po_element.order
        } for po_element in po_elements]


class PageObjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageObject
        fields = ('project', 'name', 'class_name', 'url_pattern', 'description')

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class PageObjectElementSerializer(serializers.ModelSerializer):
    page_object = PageObjectSerializer(read_only=True)
    element = ElementEnhancedSerializer(read_only=True)
    page_object_id = serializers.IntegerField(write_only=True)
    element_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = PageObjectElement
        fields = '__all__'
        read_only_fields = ('created_at',)

    def validate_method_name(self, value):
        """验证方法名称是否符合命名规范"""
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', value):
            raise serializers.ValidationError("方法名称只能包含字母、数字和下划线，且不能以数字开头")
        return value


class ScriptStepSerializer(serializers.ModelSerializer):
    """脚本结构化步骤序列化器，返回元素详情供双向编辑器回显。"""
    script = TestScriptSerializer(read_only=True)
    target_element = ElementEnhancedSerializer(read_only=True)
    page_object = PageObjectSerializer(read_only=True)

    script_id = serializers.IntegerField(write_only=True, required=False)
    target_element_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    page_object_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = ScriptStep
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

    def validate(self, data):
        """验证步骤配置"""
        target_element_id = data.get('target_element_id')
        page_object_id = data.get('page_object_id')
        action_type = data.get('action_type')

        # 某些操作类型需要指定目标元素
        if action_type in ['CLICK', 'INPUT', 'SELECT', 'HOVER', 'VERIFY'] and not target_element_id and not page_object_id:
            raise serializers.ValidationError("此操作类型需要指定目标元素或页面对象")

        return data


class ScriptElementUsageSerializer(serializers.ModelSerializer):
    script = TestScriptSerializer(read_only=True)
    element = ElementEnhancedSerializer(read_only=True)
    script_id = serializers.IntegerField(write_only=True)
    element_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = ScriptElementUsage
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class ScriptAnalysisSerializer(serializers.Serializer):
    """脚本分析结果序列化器"""
    element_usages = ScriptElementUsageSerializer(many=True, read_only=True)
    missing_elements = serializers.ListField(child=serializers.CharField(), read_only=True)
    recommendations = serializers.ListField(child=serializers.CharField(), read_only=True)
    complexity_score = serializers.IntegerField(read_only=True)


class ElementValidationSerializer(serializers.Serializer):
    """元素验证结果序列化器"""
    is_valid = serializers.BooleanField(read_only=True)
    validation_message = serializers.CharField(read_only=True)
    suggestions = serializers.ListField(child=serializers.CharField(), read_only=True)


class CodeGenerationSerializer(serializers.Serializer):
    """代码生成序列化器"""
    language = serializers.ChoiceField(choices=[('javascript', 'JavaScript'), ('python', 'Python')], default='javascript')
    framework = serializers.ChoiceField(choices=[('playwright', 'Playwright'), ('selenium', 'Selenium')], default='playwright')
    include_comments = serializers.BooleanField(default=True)

    def validate(self, data):
        # 可以添加更多验证逻辑
        return data


class TestCaseStepSerializer(serializers.ModelSerializer):
    """测试用例步骤序列化器"""
    test_case = serializers.PrimaryKeyRelatedField(
        queryset=TestCase.objects.none(), required=False, write_only=True
    )
    # 除元素主键外，同时返回定位信息，供步骤编辑器直接回显。
    element_name = serializers.CharField(source='element.name', read_only=True)
    element_page = serializers.CharField(source='element.page', read_only=True)
    element_locator = serializers.CharField(source='element.locator_value', read_only=True)
    element_locator_strategy = serializers.SerializerMethodField()
    element_locator_options = serializers.SerializerMethodField()

    class Meta:
        model = TestCaseStep
        fields = [
            'id', 'test_case', 'step_number', 'action_type', 'element', 'element_name', 'element_page', 'element_locator',
            'element_locator_strategy', 'element_locator_options',
            'input_value', 'action_params', 'wait_time', 'assert_type', 'assert_value', 'description', 'created_at'
        ]
        extra_kwargs = {
            'assert_value': {
                'help_text': 'hasAttribute 使用属性名或“属性名=期望值”，例如 href 或 href=https://example.com。',
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            accessible_projects = UiProject.objects.filter(
                Q(owner=request.user) | Q(members=request.user)
            ).distinct()
            self.fields['test_case'].queryset = TestCase.objects.filter(
                project__in=accessible_projects
            )

    def get_element_locator_strategy(self, obj):
        """返回元素关联的定位策略名称。"""
        if not obj.element_id or not obj.element.locator_strategy_id:
            return ''
        return obj.element.locator_strategy.name

    def get_element_locator_options(self, obj):
        """返回 Playwright 定位器的附加参数。"""
        return obj.element.locator_options if obj.element_id else {}

    def validate(self, attrs):
        """校验独立步骤接口提交的动作、元素和参数。"""
        action_type = attrs.get('action_type', getattr(self.instance, 'action_type', None))
        assert_type = attrs.get('assert_type', getattr(self.instance, 'assert_type', ''))
        element = attrs.get('element', getattr(self.instance, 'element', None))
        test_case = attrs.get('test_case', getattr(self.instance, 'test_case', None))
        project_id = getattr(test_case, 'project_id', None)

        action_params = attrs.get('action_params', getattr(self.instance, 'action_params', {})) or {}
        if action_type == 'storageState' and not action_params:
            raise serializers.ValidationError({'action_params': '登录态动作参数不能为空'})
        validate_test_case_step_data(
            action_type=action_type,
            assert_type=assert_type,
            input_value=attrs.get('input_value', getattr(self.instance, 'input_value', '')),
            assert_value=attrs.get('assert_value', getattr(self.instance, 'assert_value', '')),
            element=element,
            project_id=project_id,
            action_params=action_params,
        )
        return attrs


class TestCaseSerializer(serializers.ModelSerializer):
    """测试用例序列化器"""
    steps = TestCaseStepSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)

    class Meta:
        model = TestCase
        fields = [
            'id', 'name', 'description', 'folder', 'project', 'project_name', 'status', 'priority',
            'created_by', 'created_by_name', 'created_at', 'updated_at', 'steps'
        ]
        read_only_fields = ['created_by']

    def validate_folder(self, folder):
        project = self.initial_data.get('project') or getattr(self.instance, 'project_id', None)
        if folder and project and str(folder.project_id) != str(project):
            raise serializers.ValidationError('文件夹必须属于当前项目')
        return folder

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class TestCaseListSerializer(serializers.ModelSerializer):
    """列表只返回用例摘要和步骤数，完整步骤由详情接口按需加载。"""
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    steps_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = TestCase
        fields = [field for field in TestCaseSerializer.Meta.fields if field != 'steps'] + ['steps_count']


class TestCaseDetailSerializer(TestCaseSerializer):
    """详情额外返回可访问的正式测试用例，列表和写入接口不加载关联数据。"""

    linked_test_cases = serializers.SerializerMethodField()

    class Meta(TestCaseSerializer.Meta):
        fields = [*TestCaseSerializer.Meta.fields, 'linked_test_cases']

    def get_linked_test_cases(self, obj):
        from apps.projects.models import Project
        from apps.testcases.models import TestCase as FormalTestCase
        from apps.testcases.serializers import TestCaseListSerializer

        workspace_id = obj.project.workspace_project_id
        if not workspace_id:
            return []

        # 同时限制统一项目与用户权限，防止残留关联泄露其他项目的用例。
        user = self.context['request'].user
        accessible_projects = Project.objects.filter(Q(owner=user) | Q(members=user))
        cases = FormalTestCase.objects.filter(
            project_id=workspace_id,
            project__in=accessible_projects,
            automation_links__case_type='ui_test_case',
            automation_links__object_id=obj.pk,
        ).select_related('project', 'author', 'assignee', 'module').prefetch_related(
            'step_details', 'versions'
        ).order_by('id')
        return TestCaseListSerializer(cases, many=True, context=self.context).data


class TestCaseFolderSerializer(serializers.ModelSerializer):
    """UI 测试用例文件夹序列化器。"""

    class Meta:
        model = TestCaseFolder
        fields = ['id', 'project', 'name', 'parent', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

    def validate_parent(self, parent):
        project = self.initial_data.get('project') or getattr(self.instance, 'project_id', None)
        if parent and project and str(parent.project_id) != str(project):
            raise serializers.ValidationError('父文件夹必须属于当前项目')
        return parent


class TestCaseExecutionSerializer(serializers.ModelSerializer):
    """测试用例执行记录序列化器"""
    test_case_name = serializers.CharField(source='test_case.name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    test_suite_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TestCaseExecution
        fields = [
            'id', 'test_case', 'test_case_name', 'project', 'project_name',
            'test_suite', 'test_suite_name', 'execution_source', 'status',
            'engine', 'browser', 'headless', 'execution_logs', 'error_message',
            'screenshots', 'execution_time', 'started_at', 'finished_at',
            'created_by', 'created_by_name', 'created_at'
        ]
        read_only_fields = ['created_by']
    
    def get_test_suite_name(self, obj):
        """获取测试套件名称"""
        return obj.test_suite.name if obj.test_suite else None
    
    def get_created_by_name(self, obj):
        """获取创建人姓名"""
        return obj.created_by.username if obj.created_by else '-'

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class TestCaseExecutionListSerializer(TestCaseExecutionSerializer):
    """执行列表仅返回摘要，日志、错误正文和截图由详情接口按需加载。"""

    class Meta(TestCaseExecutionSerializer.Meta):
        fields = [
            field for field in TestCaseExecutionSerializer.Meta.fields
            if field not in ('execution_logs', 'error_message', 'screenshots')
        ]


class TestCaseRunSerializer(serializers.Serializer):
    """测试用例运行序列化器"""
    test_case_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    browser = serializers.ChoiceField(choices=['chrome', 'firefox', 'safari'], default='chrome')

    def validate_test_case_id(self, value):
        try:
            TestCase.objects.get(id=value)
        except TestCase.DoesNotExist:
            raise serializers.ValidationError("测试用例不存在")
        return value

    def validate_project_id(self, value):
        try:
            UiProject.objects.get(id=value)
        except UiProject.DoesNotExist:
            raise serializers.ValidationError("项目不存在")
        return value


class OperationRecordSerializer(serializers.ModelSerializer):
    """操作记录序列化器"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    operation_type_display = serializers.CharField(source='get_operation_type_display', read_only=True)
    resource_type_display = serializers.CharField(source='get_resource_type_display', read_only=True)

    class Meta:
        model = OperationRecord
        fields = [
            'id', 'operation_type', 'operation_type_display', 'resource_type',
            'resource_type_display', 'resource_id', 'resource_name', 'description',
            'user', 'user_name', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


# ==================== 定时任务和通知相关序列化器 ====================

class UiScheduledTaskSerializer(serializers.ModelSerializer):
    """UI定时任务序列化器"""
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    test_suite_name = serializers.CharField(source='test_suite.name', read_only=True)
    task_type_display = serializers.CharField(source='get_task_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    trigger_type_display = serializers.CharField(source='get_trigger_type_display', read_only=True)
    notification_type_display = serializers.SerializerMethodField()

    class Meta:
        model = UiScheduledTask
        fields = [
            'id', 'name', 'description', 'task_type', 'task_type_display',
            'trigger_type', 'trigger_type_display', 'cron_expression',
            'interval_seconds', 'execute_at', 'project', 'project_name',
            'test_suite', 'test_suite_name', 'test_cases',
            'storage_state',
            'engine', 'browser', 'headless',
            'notify_on_success', 'notify_on_failure', 'notification_type', 'notification_type_display', 'notify_emails',
            'status', 'status_display',
            'last_run_time', 'next_run_time', 'total_runs',
            'successful_runs', 'failed_runs', 'last_result', 'error_message',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'created_by', 'last_run_time', 'next_run_time', 'total_runs',
            'successful_runs', 'failed_runs', 'last_result',
            'error_message', 'created_at', 'updated_at'
        ]

    def get_notification_type_display(self, obj):
        """获取通知类型显示"""
        if obj.notification_type:
            return obj.get_notification_type_display()
        return "-"

    def validate(self, attrs):
        """验证定时任务配置"""
        trigger_type = attrs.get('trigger_type')

        if trigger_type == 'CRON':
            if not attrs.get('cron_expression'):
                raise serializers.ValidationError("Cron表达式不能为空")

        elif trigger_type == 'INTERVAL':
            if not attrs.get('interval_seconds'):
                raise serializers.ValidationError("间隔秒数不能为空")
            if attrs['interval_seconds'] < 60:
                raise serializers.ValidationError("间隔秒数不能小于60秒")

        elif trigger_type == 'ONCE':
            if not attrs.get('execute_at'):
                raise serializers.ValidationError("执行时间不能为空")
            if attrs['execute_at'] <= timezone.now():
                raise serializers.ValidationError("执行时间必须大于当前时间")

        # 验证任务类型配置
        task_type = attrs.get('task_type')
        if task_type == 'TEST_SUITE' and not attrs.get('test_suite'):
            raise serializers.ValidationError("测试套件不能为空")
        elif task_type == 'TEST_CASE':
            test_cases = attrs.get('test_cases', [])
            if not test_cases or len(test_cases) == 0:
                raise serializers.ValidationError("至少选择一个测试用例")

        project = attrs.get('project', getattr(self.instance, 'project', None))
        storage_state = attrs.get('storage_state', getattr(self.instance, 'storage_state', None))
        if storage_state and (not project or storage_state.project_id != project.id):
            raise serializers.ValidationError({'storage_state': '登录态必须属于任务关联项目'})
        if storage_state and attrs.get('engine', getattr(self.instance, 'engine', 'playwright')) != 'playwright':
            raise serializers.ValidationError({'storage_state': '只有 Playwright 任务支持登录态'})

        return attrs

    def create(self, validated_data):
        """创建定时任务"""
        validated_data['created_by'] = self.context['request'].user
        instance = super().create(validated_data)
        # 计算下次运行时间
        instance.next_run_time = instance.calculate_next_run()
        instance.save()

        # 创建对应的通知设置（如果启用了通知）
        if instance.notify_on_success or instance.notify_on_failure:
            from .models import UiTaskNotificationSetting
            from apps.core.models import UnifiedNotificationConfig

            # 确定通知类型（默认为webhook）
            notification_type = validated_data.get('notification_type', 'webhook')

            # 根据通知类型选择合适的通知配置
            notification_config = None
            if notification_type in ['webhook', 'both']:
                # 如果需要Webhook通知，优先选择Webhook配置
                notification_config = UnifiedNotificationConfig.objects.filter(
                    config_type__in=['webhook_wechat', 'webhook_feishu', 'webhook_dingtalk', 'webhook_slack'],
                    is_active=True
                ).first()

            if not notification_config:
                # 如果没有找到webhook配置或者是邮件通知，使用默认配置
                notification_config = UnifiedNotificationConfig.objects.filter(
                    is_default=True,
                    is_active=True
                ).first()

            # 创建通知设置
            UiTaskNotificationSetting.objects.create(
                task=instance,
                notification_type=notification_type,
                is_enabled=True,
                notify_on_success=instance.notify_on_success,
                notify_on_failure=instance.notify_on_failure,
                notification_config=notification_config
            )

        return instance

    def update(self, instance, validated_data):
        """更新定时任务"""
        # 更新任务基本信息
        instance = super().update(instance, validated_data)

        # 重新计算下次运行时间
        instance.next_run_time = instance.calculate_next_run()
        instance.save()

        # 更新通知设置
        if instance.notify_on_success or instance.notify_on_failure:
            from .models import UiTaskNotificationSetting
            from apps.core.models import UnifiedNotificationConfig

            # 确定通知类型（默认为webhook）
            notification_type = validated_data.get('notification_type', 'webhook')

            # 根据通知类型选择合适的通知配置
            notification_config = None
            if notification_type in ['webhook', 'both']:
                # 如果需要Webhook通知，优先选择Webhook配置
                notification_config = UnifiedNotificationConfig.objects.filter(
                    config_type__in=['webhook_wechat', 'webhook_feishu', 'webhook_dingtalk', 'webhook_slack'],
                    is_active=True
                ).first()

            if not notification_config:
                # 如果没有找到webhook配置或者是邮件通知，使用默认配置
                notification_config = UnifiedNotificationConfig.objects.filter(
                    is_default=True,
                    is_active=True
                ).first()

            # 获取或创建通知设置
            notification_setting, created = UiTaskNotificationSetting.objects.get_or_create(
                task=instance,
                defaults={
                    'notification_type': notification_type,
                    'is_enabled': True,
                    'notify_on_success': instance.notify_on_success,
                    'notify_on_failure': instance.notify_on_failure,
                    'notification_config': notification_config
                }
            )

            # 如果通知设置已存在，更新它
            if not created:
                notification_setting.notification_type = notification_type
                notification_setting.is_enabled = True
                notification_setting.notify_on_success = instance.notify_on_success
                notification_setting.notify_on_failure = instance.notify_on_failure
                notification_setting.notification_config = notification_config
                notification_setting.save()
        else:
            # 如果不需要通知，禁用通知设置
            from .models import UiTaskNotificationSetting
            UiTaskNotificationSetting.objects.filter(task=instance).update(is_enabled=False)

        return instance


class AICaseSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    created_by = UserSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = AICase
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'created_by')

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class AIExecutionRecordSerializer(serializers.ModelSerializer):
    project = UiProjectSerializer(read_only=True)
    ai_case = AICaseSerializer(read_only=True)
    executed_by = UserSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True)
    ai_case_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    ai_case_name = serializers.CharField(source='ai_case.name', read_only=True)
    executed_by_name = serializers.CharField(source='executed_by.username', read_only=True)

    class Meta:
        model = AIExecutionRecord
        fields = [
            'id', 'project', 'project_id', 'project_name', 'ai_case', 'ai_case_id', 'ai_case_name', 'case_name',
            'task_description',
            'execution_mode', 'status', 'start_time', 'end_time', 'duration',
            'logs', 'steps_completed', 'planned_tasks', 'executed_by', 'executed_by_name',
            'gif_path', 'screenshots_sequence'
        ]
        read_only_fields = ('start_time', 'end_time', 'duration', 'executed_by', 'gif_path', 'screenshots_sequence')



class UiNotificationLogSerializer(serializers.ModelSerializer):
    """UI通知日志序列化器"""
    recipient_names = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    notification_type_display = serializers.CharField(source='get_notification_type_display', read_only=True)
    retry_status = serializers.SerializerMethodField()
    task_type_display = serializers.SerializerMethodField()
    actual_notification_type_display = serializers.SerializerMethodField()

    class Meta:
        model = UiNotificationLog
        fields = [
            'id', 'task', 'task_name', 'notification_type',
            'notification_type_display', 'actual_notification_type_display', 'task_type_display',
            'sender_name', 'sender_email',
            'recipient_names', 'webhook_bot_info', 'notification_content',
            'status', 'status_display', 'error_message', 'response_info',
            'created_at', 'sent_at', 'retry_count', 'retry_status'
        ]
        read_only_fields = ['created_at', 'sent_at']

    def get_recipient_names(self, obj):
        """获取收件人姓名列表"""
        return obj.get_recipient_names()

    def get_retry_status(self, obj):
        """获取重试状态"""
        return obj.get_retry_status()

    def get_task_type_display(self, obj):
        """获取任务类型显示 - 使用保存的快照值"""
        if obj.task_type:
            # 使用保存的任务类型快照
            task_type_choices = dict(UiScheduledTask.TASK_TYPE_CHOICES)
            return task_type_choices.get(obj.task_type, obj.task_type)
        # 如果 task_type 为空，返回未记录，不要从 task 对象获取（避免显示修改后的值）
        return '未记录'

    def get_actual_notification_type_display(self, obj):
        """获取实际的通知类型显示 - 根据实际发送的通知来判断"""
        # 优先检查webhook_bot_info,如果存在则说明是webhook通知
        if obj.webhook_bot_info:
            bot_type = obj.webhook_bot_info.get('bot_type', '') or obj.webhook_bot_info.get('type', '')
            # 根据机器人类型返回友好名称
            type_map = {
                'wechat': '企微机器人',
                'feishu': '飞书机器人',
                'dingtalk': '钉钉机器人',
                'slack': 'Slack 机器人'
            }
            return type_map.get(bot_type, 'Webhook机器人')

        # 检查recipient_info,如果存在则说明是邮箱通知
        if obj.recipient_info:
            if isinstance(obj.recipient_info, list) and len(obj.recipient_info) > 0:
                return '邮箱通知'
            elif isinstance(obj.recipient_info, dict) and obj.recipient_info.get('email'):
                return '邮箱通知'

        # 如果都没有,回退到任务的notification_type
        if obj.task:
            notification_type = obj.task.notification_type
            type_map = {
                'email': '邮箱通知',
                'webhook': 'Webhook机器人',
                'both': '两种都发送'
            }
            return type_map.get(notification_type, notification_type)

        return '-'


class UiTaskNotificationSettingSerializer(serializers.ModelSerializer):
    """UI任务通知设置序列化器"""
    notification_type_display = serializers.CharField(source='get_notification_type_display', read_only=True)
    notification_config_name = serializers.CharField(source='notification_config.name', read_only=True)
    active_types = serializers.SerializerMethodField()

    class Meta:
        model = UiTaskNotificationSetting
        fields = [
            'id', 'task', 'notification_type', 'notification_type_display',
            'notification_config', 'notification_config_name', 'is_enabled',
            'notify_on_success', 'notify_on_failure', 'notify_on_timeout',
            'notify_on_error', 'custom_webhook_bots', 'active_types',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_active_types(self, obj):
        """获取激活的通知类型"""
        types = obj.get_active_notification_types()
        type_names = []
        if 'email' in types:
            type_names.append('邮箱')
        if 'webhook' in types:
            type_names.append('Webhook机器人')
        return ', '.join(type_names) if type_names else "无"

