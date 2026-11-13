from rest_framework import mixins, viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from .models import TestPlan, TestRun, TestRunCase, TestRunCaseHistory
from apps.testcases.models import TestCase
from apps.projects.models import Project
from apps.users.models import User
from .serializers import (TestPlanSerializer, TestRunSerializer, TestRunListSerializer,
                         TestRunCaseSerializer, TestPlanDetailSerializer,
                         TestRunCaseDetailSerializer, TestRunCaseHistorySerializer)

class TestPlanViewSet(viewsets.ModelViewSet):
    """
    测试计划视图集
    """
    queryset = TestPlan.objects.all().order_by('-created_at')
    serializer_class = TestPlanSerializer

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TestPlanDetailSerializer
        return TestPlanSerializer

    def _detail_queryset(self):
        """计划详情的一次性预取链。"""
        return TestPlan.objects.select_related('creator', 'version').prefetch_related(
            # 不预取 projects：序列化器直接 values_list('id', 'name') 读 M2M，
            # 只取两列、不加载 Project 实例，避免把整个项目行拉回来
            'assignees',
            Prefetch(
                'test_runs',
                queryset=TestRun.objects.select_related('project', 'assignee').prefetch_related(
                    Prefetch(
                        'run_cases',
                        queryset=TestRunCase.objects.select_related(
                            'testcase__module',
                            'testcase__author',
                            'testcase__assignee',
                            'testcase__project',  # 序列化 testcase.project_name 需要
                            'executed_by',
                        ).prefetch_related('testcase__step_details').order_by('testcase__id')
                    )
                )
            ),
        )

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != 'retrieve':
            return queryset
        return self._detail_queryset()

    @staticmethod
    def _default_executor(test_plan):
        """
        新增执行用例的默认执行人：仅当计划「指派给」恰好一人时取该人。
        """
        assignees = list(test_plan.assignees.order_by('id')[:2])
        return assignees[0] if len(assignees) == 1 else None

    def _sync_test_runs(self, test_plan, project_ids, testcase_ids):
        if not project_ids:
            test_plan.projects.clear()
            test_plan.test_runs.all().delete()
            return

        valid_projects = list(Project.objects.filter(id__in=project_ids))
        valid_project_ids = [project.id for project in valid_projects]
        test_plan.projects.set(valid_project_ids)

        default_executor = self._default_executor(test_plan)

        testcase_queryset = TestCase.objects.filter(
            id__in=testcase_ids,
            project_id__in=valid_project_ids
        ).select_related('project')
        testcase_map = {testcase.id: testcase for testcase in testcase_queryset}

        existing_runs = {test_run.project_id: test_run for test_run in test_plan.test_runs.all()}

        for project_id, test_run in list(existing_runs.items()):
            if project_id not in valid_project_ids:
                test_run.delete()
                existing_runs.pop(project_id, None)

        for project in valid_projects:
            test_run = existing_runs.get(project.id)
            if not test_run:
                test_run = TestRun.objects.create(
                    name=f"{test_plan.name} - {project.name} Execution",
                    test_plan=test_plan,
                    project=project,
                    version=test_plan.version,
                    creator=test_plan.creator,
                    assignee=test_plan.creator
                )
                existing_runs[project.id] = test_run
            else:
                test_run.name = f"{test_plan.name} - {project.name} Execution"
                test_run.version = test_plan.version
                test_run.save(update_fields=['name', 'version', 'updated_at'])

            run_testcases = [testcase for testcase in testcase_queryset if testcase.project_id == project.id]
            TestRunCase.objects.filter(test_run=test_run).exclude(
                testcase_id__in=[testcase.id for testcase in run_testcases]
            ).delete()

            existing_case_ids = set(
                TestRunCase.objects.filter(test_run=test_run).values_list('testcase_id', flat=True)
            )
            new_cases = [
                TestRunCase(
                    test_run=test_run,
                    testcase=testcase,
                    priority=testcase.priority,
                    executed_by=default_executor,
                )
                for testcase in run_testcases
                if testcase.id not in existing_case_ids
            ]
            if new_cases:
                TestRunCase.objects.bulk_create(new_cases)

            test_run.testcases.set([testcase.id for testcase in run_testcases])

    def perform_create(self, serializer):
        # 在创建TestPlan时，设置creator并自动为每个项目创建TestRun和TestRunCase
        # 获取版本信息
        version_id = self.request.data.get('version')
        version = None
        if version_id:
            from apps.versions.models import Version
            try:
                version = Version.objects.get(id=version_id)
            except Version.DoesNotExist:
                pass
        
        test_plan = serializer.save(creator=self.request.user, version=version)

        # 「指派给」不在 TestPlanSerializer 的 fields 里，需手动写入；
        # 且必须在 _sync_test_runs 之前，新增用例才能拿到默认执行人
        assignee_ids = self.request.data.get('assignees') or []
        if assignee_ids:
            test_plan.assignees.set(assignee_ids)

        project_ids = self.request.data.get('projects', [])
        testcase_ids = self.request.data.get('testcases', [])
        self._sync_test_runs(test_plan, project_ids, testcase_ids)

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
            self._sync_test_runs(test_plan, project_ids, valid_ids)

        # 用详情预取链重新取一次，避免返回时按 run_cases / testcase 逐条回查
        test_plan = self._detail_queryset().get(pk=test_plan.pk)
        return Response(TestPlanDetailSerializer(test_plan).data)

    def perform_update(self, serializer):
        # 在更新TestPlan时，处理版本信息
        version_id = self.request.data.get('version')
        version = None
        if version_id:
            from apps.versions.models import Version
            try:
                version = Version.objects.get(id=version_id)
            except Version.DoesNotExist:
                pass
        
        with transaction.atomic():
            test_plan = serializer.save(version=version)
            data = self.request.data

            # 只处理请求里真正携带的字段。列表页的状态开关是 PATCH {is_active}，
            if 'assignees' in data:
                # 先落「指派给」：_sync_test_runs 要用它给新增用例设置默认执行人，
                assignee_ids = data.get('assignees') or []
                if assignee_ids:
                    test_plan.assignees.set(assignee_ids)
                else:
                    test_plan.assignees.clear()

            if 'projects' in data or 'testcases' in data:
                if 'projects' in data:
                    project_ids = data.get('projects') or []
                else:
                    project_ids = list(test_plan.projects.values_list('id', flat=True))
                if 'testcases' in data:
                    testcase_ids = data.get('testcases') or []
                else:
                    testcase_ids = list(
                        TestRunCase.objects.filter(test_run__test_plan=test_plan)
                        .values_list('testcase_id', flat=True)
                    )
                self._sync_test_runs(test_plan, project_ids, testcase_ids)


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
