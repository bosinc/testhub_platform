from rest_framework import serializers
from .models import TestPlan, TestRun, TestRunCase, TestRunCaseHistory
from apps.testcases.models import TestCase
from apps.testcases.serializers import TestCaseStepSerializer, TestCaseModuleSimpleSerializer
from apps.projects.serializers import ProjectSimpleSerializer
from apps.users.serializers import UserSimpleSerializer

class TestRunCaseHistorySerializer(serializers.ModelSerializer):
    executed_by = UserSimpleSerializer(read_only=True)
    
    class Meta:
        model = TestRunCaseHistory
        fields = ('id', 'status', 'actual_result', 'comments', 'executed_by', 'executed_at')

class TestCaseExecutionSerializer(serializers.ModelSerializer):
    """测试计划详情中用于展示和执行弹窗的用例信息。"""
    author = UserSimpleSerializer(read_only=True)
    assignee = UserSimpleSerializer(read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    module = TestCaseModuleSimpleSerializer(read_only=True)
    steps = TestCaseStepSerializer(source='step_details', many=True, read_only=True)
    automation_links = serializers.SerializerMethodField()

    class Meta:
        model = TestCase
        fields = (
            'id', 'title', 'description', 'notes', 'preconditions', 'steps',
            'priority', 'test_type', 'automatability', 'automation_links', 'author', 'assignee',
            'project_name', 'module'
        )

    def get_automation_links(self, obj):
        """返回测试计划用例列表展示自动化图标所需的关联类型。"""
        return [
            {'id': link.id, 'type': link.case_type, 'object_id': link.object_id}
            for link in obj.automation_links.all()
        ]

class TestRunCaseSimpleSerializer(serializers.ModelSerializer):
    testcase = TestCaseExecutionSerializer(read_only=True)
    executed_by = UserSimpleSerializer(read_only=True)

    class Meta:
        model = TestRunCase
        fields = ('id', 'testcase', 'status', 'priority', 'actual_result', 'comments', 'executed_by', 'executed_at')

class TestRunCaseDetailSerializer(serializers.ModelSerializer):
    """执行用例详情，供 update_status 的响应使用。
    """
    testcase = TestCaseExecutionSerializer(read_only=True)
    executed_by = UserSimpleSerializer(read_only=True)

    class Meta:
        model = TestRunCase
        fields = ('id', 'testcase', 'status', 'priority', 'actual_result', 'comments',
                 'defects', 'elapsed_time', 'executed_by', 'executed_at', 'created_at',
                 'updated_at')

class TestRunListSerializer(serializers.ModelSerializer):
    """执行记录列表：只返回 run 自身字段 + 进度，不内嵌 run_cases 明细。"""
    assignee = UserSimpleSerializer(read_only=True)
    project = ProjectSimpleSerializer(read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = TestRun
        fields = ('id', 'name', 'status', 'assignee', 'project', 'progress')

    def get_progress(self, obj):
        return obj.progress_stats


class TestRunSerializer(serializers.ModelSerializer):
    """执行记录详情：内嵌 run_cases 明细（计划详情页按模块树展示用例用）。"""
    assignee = UserSimpleSerializer(read_only=True)
    run_cases = TestRunCaseSimpleSerializer(many=True, read_only=True)
    project = ProjectSimpleSerializer(read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = TestRun
        fields = ('id', 'name', 'status', 'assignee', 'project', 'progress', 'run_cases')
    
    def get_progress(self, obj):
        return obj.progress_stats


class TestPlanSerializer(serializers.ModelSerializer):
    creator = UserSimpleSerializer(read_only=True)
    projects = serializers.StringRelatedField(many=True, read_only=True)
    version = serializers.StringRelatedField()
    execution_status = serializers.SerializerMethodField()

    class Meta:
        model = TestPlan
        fields = ('id', 'name', 'projects', 'version', 'creator', 'created_at', 'is_active', 'execution_status')

    def get_execution_status(self, obj):
        """按执行用例进度汇总计划状态，供列表筛选和展示使用。"""
        prefetched_runs = getattr(obj, '_prefetched_objects_cache', {}).get('test_runs')
        if prefetched_runs is not None:
            statuses = [
                run_case.status
                for test_run in prefetched_runs
                for run_case in getattr(test_run, '_prefetched_objects_cache', {}).get('run_cases', [])
            ]
        else:
            statuses = list(
                TestRunCase.objects.filter(test_run__test_plan=obj)
                .values_list('status', flat=True)
            )
        if not statuses:
            return 'not_started'
        if all(value == 'untested' for value in statuses):
            return 'not_started'
        if all(value != 'untested' for value in statuses):
            return 'completed'
        return 'in_progress'


class TestPlanDetailSerializer(serializers.ModelSerializer):
    """测试计划详情。"""
    test_runs = TestRunSerializer(many=True, read_only=True)
    creator = UserSimpleSerializer(read_only=True)
    version = serializers.StringRelatedField()
    projects = serializers.SerializerMethodField()

    class Meta:
        model = TestPlan
        fields = (
            'id', 'name', 'description', 'version', 'creator', 'assignees', 'is_active',
            'created_at', 'updated_at', 'projects', 'test_runs',
        )

    def get_projects(self, obj):
        """计划绑定项目 [{id, name}]，以 M2M 为准。"""
        return [
            {'id': project_id, 'name': name}
            for project_id, name in obj.projects.values_list('id', 'name')
        ]

class TestRunCaseSerializer(serializers.ModelSerializer):
    """仅作为 TestRunCaseViewSet 的 serializer_class 占位。"""

    class Meta:
        model = TestRunCase
        fields = '__all__'
