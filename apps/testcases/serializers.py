from rest_framework import serializers
from .models import TestCase, TestCaseAutomationLink, TestCaseModule, TestCaseStep, TestCaseAttachment, TestCaseComment, TestCaseImportRecord
from .steps import STEP_TEXT_MAX_LENGTH, sync_steps, validate_step_payload
from apps.users.serializers import UserSerializer
from apps.versions.serializers import VersionSimpleSerializer

class TestCaseStepSerializer(serializers.ModelSerializer):
    """步骤与预期结果一一对应，sort_order 决定展示顺序。
    """
    id = serializers.IntegerField(required=False, allow_null=True)
    sort_order = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    action = serializers.CharField(
        max_length=STEP_TEXT_MAX_LENGTH, required=False, allow_blank=True,
        error_messages={'max_length': f'步骤描述不能超过 {STEP_TEXT_MAX_LENGTH} 字符'})
    expected = serializers.CharField(
        max_length=STEP_TEXT_MAX_LENGTH, required=False, allow_blank=True,
        error_messages={'max_length': f'预期结果不能超过 {STEP_TEXT_MAX_LENGTH} 字符'})

    class Meta:
        model = TestCaseStep
        fields = ['id', 'sort_order', 'action', 'expected']

    def validate_action(self, value):
        return (value or '').strip()

    def validate_expected(self, value):
        return (value or '').strip()

class TestCaseAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserSerializer(read_only=True)
    
    class Meta:
        model = TestCaseAttachment
        fields = '__all__'

class TestCaseCommentSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    
    class Meta:
        model = TestCaseComment
        fields = '__all__'

class ProjectSimpleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class TestCaseModuleSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCaseModule
        fields = ['id', 'name', 'project_id', 'parent_id']

class TestCaseSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    assignee = UserSerializer(read_only=True)
    project = ProjectSimpleSerializer(read_only=True)
    versions = VersionSimpleSerializer(many=True, read_only=True)
    steps = TestCaseStepSerializer(source='step_details', many=True, read_only=True)
    attachments = TestCaseAttachmentSerializer(many=True, read_only=True)
    comments = TestCaseCommentSerializer(many=True, read_only=True)
    module = TestCaseModuleSimpleSerializer(read_only=True)
    
    class Meta:
        model = TestCase
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']

class TestCaseListSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    project = serializers.SerializerMethodField()
    versions = serializers.SerializerMethodField()
    module = TestCaseModuleSimpleSerializer(read_only=True)
    steps = TestCaseStepSerializer(source='step_details', many=True, read_only=True)
    automation_links = serializers.SerializerMethodField()
    
    class Meta:
        model = TestCase
        fields = [
            'id', 'title', 'description', 'notes', 'preconditions', 'steps',
            'priority', 'test_type',
            'author', 'assignee', 'project', 'module', 'versions', 'tags', 'automation_links', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_author(self, obj):
        return {'id': obj.author.id, 'username': obj.author.username} if obj.author else None

    def get_assignee(self, obj):
        return {'id': obj.assignee.id, 'username': obj.assignee.username} if obj.assignee else None
    
    def get_project(self, obj):
        return {'id': obj.project.id, 'name': obj.project.name} if obj.project else None
    
    def get_versions(self, obj):
        return [{'id': v.id, 'name': v.name, 'is_baseline': v.is_baseline} for v in obj.versions.all()]

    def get_automation_links(self, obj):
        return self.context.get('automation_links_by_test_case', {}).get(obj.id, [])

class TestCaseCreateSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField(required=False, allow_null=True, help_text="项目ID，可选")
    version_ids = serializers.ListField(
        child=serializers.IntegerField(), 
        required=False, 
        allow_empty=True,
        help_text="关联版本ID列表"
    )
    maintainer_id = serializers.IntegerField(required=False, write_only=True)
    module_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    steps = TestCaseStepSerializer(many=True, allow_empty=False)
    
    class Meta:
        model = TestCase
        fields = [
            'title', 'description', 'notes', 'preconditions', 'steps',
            'priority', 'test_type', 'status', 'tags', 'project_id', 'version_ids', 'maintainer_id', 'module_id'
        ]
    
    def validate_steps(self, value):
        errors = validate_step_payload(value)
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def create(self, validated_data):
        version_ids = validated_data.pop('version_ids', [])
        steps = validated_data.pop('steps', [])
        # project_id会在视图的perform_create中处理
        validated_data.pop('project_id', None)
        validated_data.pop('module_id', None)
        
        testcase = super().create(validated_data)
        sync_steps(testcase, steps)
        
        # 设置版本关联
        if version_ids:
            testcase.versions.set(version_ids)
        
        return testcase

    def to_representation(self, instance):
        # 写入用 steps 数组，响应统一按详情结构返回（含步骤与关联信息）。
        return TestCaseSerializer(instance, context=self.context).data

class TestCaseUpdateSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField(required=False, allow_null=True, help_text="项目ID，可选")
    version_ids = serializers.ListField(
        child=serializers.IntegerField(), 
        required=False, 
        allow_empty=True,
        help_text="关联版本ID列表"
    )
    maintainer_id = serializers.IntegerField(required=False, write_only=True)
    module_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    steps = TestCaseStepSerializer(many=True, required=False, allow_empty=False)
    
    class Meta:
        model = TestCase
        fields = [
            'title', 'description', 'notes', 'preconditions', 'steps',
            'priority', 'test_type', 'status', 'tags', 'project_id', 'version_ids', 'maintainer_id', 'module_id'
        ]

    def validate_steps(self, value):
        """步骤 id 必须唯一且属于当前用例，避免互相覆盖或改走别的用例的步骤。"""
        instance = getattr(self, 'instance', None)
        allowed_ids = None
        if instance is not None:
            allowed_ids = set(instance.step_details.values_list('id', flat=True))
        errors = validate_step_payload(value, allowed_ids=allowed_ids)
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def update(self, instance, validated_data):
        version_ids = validated_data.pop('version_ids', None)
        maintainer_id = validated_data.pop('maintainer_id', None)
        steps = validated_data.pop('steps', None)
        validated_data.pop('module_id', None)
        # project_id会在视图中处理
        validated_data.pop('project_id', None)
        
        instance = super().update(instance, validated_data)
        if maintainer_id is not None:
            instance.author_id = maintainer_id
            instance.save(update_fields=['author'])
        if steps is not None:
            sync_steps(instance, steps)
        
        # 更新版本关联
        if version_ids is not None:
            instance.versions.set(version_ids)

        return instance

    def to_representation(self, instance):
        # 写入用 steps 数组，响应统一按详情结构返回（含步骤与关联信息）。
        return TestCaseSerializer(instance, context=self.context).data


class TestCaseImportRecordListSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source='project.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = TestCaseImportRecord
        fields = [
            'id', 'import_no', 'project', 'project_name', 'status', 'progress',
            'total_rows', 'success_count', 'failed_count', 'skip_count',
            'error_message', 'template_version', 'created_by_name',
            'created_at', 'completed_at'
        ]


class TestCaseImportRecordDetailSerializer(TestCaseImportRecordListSerializer):
    failure_report_url = serializers.SerializerMethodField()

    class Meta(TestCaseImportRecordListSerializer.Meta):
        fields = TestCaseImportRecordListSerializer.Meta.fields + [
            'failure_details', 'failure_report_file', 'failure_report_url'
        ]

    def get_failure_report_url(self, obj):
        if obj.failure_report_file:
            return obj.failure_report_file.url
        return None
