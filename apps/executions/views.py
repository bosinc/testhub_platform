from rest_framework import mixins, viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.utils import timezone
from . import services
from .models import TestPlan, TestRun, TestRunCase, TestRunCaseHistory
from apps.testcases.models import TestCase
from apps.users.models import User
from .serializers import (TestPlanSerializer, TestRunSerializer, TestRunListSerializer,
                         TestRunCaseSerializer, TestPlanDetailSerializer,
                         TestRunCaseDetailSerializer, TestRunCaseHistorySerializer)

class TestPlanViewSet(viewsets.ModelViewSet):
    """
    测试计划视图集

    计划的创建、更新与执行记录同步在 services.py，与 MCP 工具共用。
    """
    queryset = TestPlan.objects.all().order_by('-created_at')
    serializer_class = TestPlanSerializer

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TestPlanDetailSerializer
        return TestPlanSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != 'retrieve':
            params = self.request.query_params
            is_active = params.get('is_active')
            project_id = params.get('project')
            execution_status = params.get('execution_status')

            if is_active in {'true', '1'}:
                queryset = queryset.filter(is_active=True)
            elif is_active in {'false', '0'}:
                queryset = queryset.filter(is_active=False)

            if project_id:
                queryset = queryset.filter(projects__id=project_id)

            run_cases = TestRunCase.objects.filter(test_run__test_plan=OuterRef('pk'))
            has_run_cases = Exists(run_cases)
            has_untested_cases = Exists(run_cases.filter(status='untested'))
            has_tested_cases = Exists(run_cases.exclude(status='untested'))
            if execution_status == 'not_started':
                queryset = queryset.annotate(
                    has_tested_cases=has_tested_cases,
                ).filter(has_tested_cases=False)
            elif execution_status == 'in_progress':
                queryset = queryset.annotate(
                    has_untested_cases=has_untested_cases,
                    has_tested_cases=has_tested_cases,
                ).filter(has_untested_cases=True, has_tested_cases=True)
            elif execution_status == 'completed':
                queryset = queryset.annotate(
                    has_run_cases=has_run_cases,
                    has_untested_cases=has_untested_cases,
                ).filter(has_run_cases=True, has_untested_cases=False)

            return queryset.prefetch_related(
                Prefetch(
                    'test_runs__run_cases',
                    queryset=TestRunCase.objects.only('id', 'test_run_id', 'status'),
                )
            ).distinct()
        return services.plan_detail_queryset()

    def perform_create(self, serializer):
        # 版本 / 指派人 / 项目 / 用例不在 TestPlanSerializer 的可写字段里，
        # 从请求体取出后交给序列化器，由它转给 services.create_test_plan
        data = self.request.data
        serializer.save(
            creator=self.request.user,
            version_id=data.get('version'),
            assignee_ids=data.get('assignees') or [],
            project_ids=data.get('projects', []),
            testcase_ids=data.get('testcases', []),
        )

    @action(detail=True, methods=['post'])
    def link_testcases(self, request, pk=None):
        """为已有测试计划关联/取消关联测试用例，并同步各项目的执行记录。"""
        test_plan = self.get_object()
        testcase_ids = request.data.get('testcases')
        if testcase_ids is None:
            return Response({'error': 'testcases is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(testcase_ids, list):
            return Response({'error': 'testcases must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        project_ids = list(test_plan.projects.values_list('id', flat=True))
        if not project_ids:
            return Response({'error': 'plan has no related projects'}, status=status.HTTP_400_BAD_REQUEST)

        # 只保留属于该计划关联项目的用例，避免跨项目串数据
        valid_ids = list(
            TestCase.objects.filter(id__in=testcase_ids, project_id__in=project_ids)
            .values_list('id', flat=True)
        )

        with transaction.atomic():
            services.sync_test_runs(test_plan, project_ids, valid_ids)

        # 用详情预取链重新取一次，避免返回时按 run_cases / testcase 逐条回查
        test_plan = services.plan_detail_queryset().get(pk=test_plan.pk)
        return Response(TestPlanDetailSerializer(test_plan).data)

    def perform_update(self, serializer):
        # 只处理请求里真正携带的字段。列表页的状态开关是 PATCH {is_active}，
        # 没带的版本 / 指派人 / 项目 / 用例一律保持原样
        data = self.request.data
        relations = {}
        if 'version' in data:
            relations['version_id'] = data.get('version')
        if 'assignees' in data:
            relations['assignee_ids'] = data.get('assignees') or []
        if 'projects' in data:
            relations['project_ids'] = data.get('projects') or []
        if 'testcases' in data:
            relations['testcase_ids'] = data.get('testcases') or []
        serializer.save(**relations)


class TestRunViewSet(viewsets.ReadOnlyModelViewSet):
    """
    测试执行记录（只读）。
    """
    queryset = TestRun.objects.all().order_by('-created_at')
    serializer_class = TestRunSerializer

    def get_serializer_class(self):
        if self.action == 'list':
            return TestRunListSerializer
        return TestRunSerializer

    def get_queryset(self):
        queryset = super().get_queryset().select_related('project', 'assignee')
        if self.action == 'list':
            return queryset.prefetch_related(
                Prefetch(
                    'run_cases',
                    queryset=TestRunCase.objects.only('id', 'test_run_id', 'status').order_by(),
                )
            )
        if self.action == 'retrieve':
            return queryset.prefetch_related(
                Prefetch(
                    'run_cases',
                    queryset=TestRunCase.objects.select_related(
                        'testcase__module',
                        'testcase__author',
                        'testcase__assignee',
                        'testcase__project',
                        'executed_by',
                    ).prefetch_related('testcase__step_details').order_by('testcase__id')
                )
            )
        return queryset

class TestRunCaseViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    测试执行用例视图集
    """
    queryset = TestRunCase.objects.all()
    serializer_class = TestRunCaseSerializer

    @action(detail=True, methods=['patch'])
    def update_status(self, request, pk=None):
        """
        更新单个用例的执行状态，并自动创建历史记录
        """
        run_case = self.get_object()
        new_status = request.data.get('status')
        actual_result = request.data.get('actual_result', '')
        comments = request.data.get('comments', '')
        
        if not new_status:
            return Response({'error': 'Status is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # 创建历史记录
        TestRunCaseHistory.objects.create(
            run_case=run_case,
            status=new_status,
            actual_result=actual_result,
            comments=comments,
            executed_by=request.user,
            executed_at=timezone.now()
        )
        
        # 更新执行用例状态
        run_case.status = new_status
        run_case.actual_result = actual_result
        run_case.comments = comments
        run_case.executed_by = request.user
        run_case.executed_at = timezone.now()
        run_case.save()
        
        return Response(TestRunCaseDetailSerializer(run_case).data)

    @action(detail=False, methods=['post'])
    def batch_update(self, request):
        """
        批量更新执行用例：支持统一设置执行人 / 执行结果。
        """
        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return Response({'error': 'ids is required'}, status=status.HTTP_400_BAD_REQUEST)

        status_value = request.data.get('status')
        if status_value and status_value not in dict(TestRunCase.STATUS_CHOICES):
            return Response({'error': 'invalid status'}, status=status.HTTP_400_BAD_REQUEST)

        assignee_id = request.data.get('executed_by')
        assignee = None
        if assignee_id:
            assignee = User.objects.filter(pk=assignee_id).first()
            if not assignee:
                return Response({'error': 'user not found'}, status=status.HTTP_400_BAD_REQUEST)

        run_cases = list(TestRunCase.objects.filter(pk__in=ids))
        if not run_cases:
            return Response({'error': 'no matching run cases'}, status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        with transaction.atomic():
            for run_case in run_cases:
                if assignee is not None:
                    run_case.executed_by = assignee
                if status_value:
                    run_case.status = status_value
                    run_case.executed_at = now
                    if assignee is None:
                        run_case.executed_by = request.user
                    TestRunCaseHistory.objects.create(
                        run_case=run_case,
                        status=status_value,
                        actual_result=run_case.actual_result or '',
                        comments=run_case.comments or '',
                        executed_by=run_case.executed_by or request.user,
                        executed_at=now,
                    )
                run_case.save()

        return Response({'updated': len(run_cases)})

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        """
        获取用例执行历史记录
        """
        run_case = self.get_object()
        history = run_case.history.all().order_by('-executed_at')
        serializer = TestRunCaseHistorySerializer(history, many=True)
        return Response(serializer.data)
