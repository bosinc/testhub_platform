"""测试计划的可见范围、详情预取与写入逻辑。

REST 视图（TestPlanViewSet）与 MCP 工具共用本模块：
计划的创建、更新与执行记录同步只在这里实现一份。
"""
from django.db import transaction
from django.db.models import Prefetch, Q

from apps.projects.models import Project
from apps.testcases.models import TestCase
from apps.versions.models import Version
from .models import TestPlan, TestRun, TestRunCase

#: 「未传该参数」的占位：区别于显式传 None（清空）
UNSET = object()


def accessible_test_plans(user):
    """用户可见的测试计划：本人创建、被指派，或关联项目里有本人。"""
    projects = Project.objects.filter(Q(owner=user) | Q(members=user))
    return TestPlan.objects.filter(
        Q(creator=user) | Q(assignees=user) | Q(projects__in=projects)
    ).distinct()


def plan_detail_queryset():
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
                    ).prefetch_related(
                        'testcase__step_details',
                        'testcase__automation_links',
                    ).order_by('testcase__id')
                )
            )
        ),
    )


def default_executor(test_plan):
    """
    新增执行用例的默认执行人：仅当计划「指派给」恰好一人时取该人。
    """
    assignees = list(test_plan.assignees.order_by('id')[:2])
    return assignees[0] if len(assignees) == 1 else None


def sync_test_runs(test_plan, project_ids, testcase_ids):
    """把计划的关联项目、执行记录与执行用例同步为给定集合。

    差量同步：仍在集合里的执行用例原样保留（含执行结果），
    只删除被移出的、新增缺少的；不属于关联项目的用例会被忽略。
    """
    if not project_ids:
        test_plan.projects.clear()
        test_plan.test_runs.all().delete()
        return

    valid_projects = list(Project.objects.filter(id__in=project_ids))
    valid_project_ids = [project.id for project in valid_projects]
    test_plan.projects.set(valid_project_ids)

    executor = default_executor(test_plan)

    testcase_queryset = TestCase.objects.filter(
        id__in=testcase_ids,
        project_id__in=valid_project_ids
    ).select_related('project')

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
                executed_by=executor,
            )
            for testcase in run_testcases
            if testcase.id not in existing_case_ids
        ]
        if new_cases:
            TestRunCase.objects.bulk_create(new_cases)

        test_run.testcases.set([testcase.id for testcase in run_testcases])


def _resolve_version(version_id):
    """按 ID 取版本；未传或不存在返回 None。"""
    if not version_id:
        return None
    return Version.objects.filter(id=version_id).first()


@transaction.atomic
def create_test_plan(*, creator, fields, version_id=None, assignee_ids=None,
                     project_ids=None, testcase_ids=None):
    """创建测试计划，并为每个关联项目生成执行记录与执行用例。

    fields 是 TestPlan 自身的普通字段（name / description / is_active 等）。
    整体在一个事务里：执行记录同步失败时不会留下半成品计划。
    """
    test_plan = TestPlan.objects.create(
        creator=creator, version=_resolve_version(version_id), **fields)

    # 「指派给」必须先于 sync_test_runs 落库，新增执行用例才能拿到默认执行人
    if assignee_ids:
        test_plan.assignees.set(assignee_ids)

    sync_test_runs(test_plan, project_ids or [], testcase_ids or [])
    return test_plan


@transaction.atomic
def update_test_plan(test_plan, *, fields=None, version_id=UNSET, assignee_ids=None,
                     project_ids=None, testcase_ids=None):
    """更新测试计划，只处理显式传入的部分（None / UNSET 表示该部分不改）。

    project_ids、testcase_ids 任一传入即同步执行记录，另一项沿用计划当前值。
    """
    for name, value in (fields or {}).items():
        setattr(test_plan, name, value)
    if version_id is not UNSET:
        test_plan.version = _resolve_version(version_id)
    test_plan.save()

    if assignee_ids is not None:
        # 先落「指派给」：sync_test_runs 要用它给新增用例设置默认执行人
        test_plan.assignees.set(assignee_ids)

    if project_ids is not None or testcase_ids is not None:
        if project_ids is None:
            project_ids = list(test_plan.projects.values_list('id', flat=True))
        if testcase_ids is None:
            testcase_ids = list(
                TestRunCase.objects.filter(test_run__test_plan=test_plan)
                .values_list('testcase_id', flat=True)
            )
        sync_test_runs(test_plan, project_ids, testcase_ids)
    return test_plan
