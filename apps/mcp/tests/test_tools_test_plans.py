# -*- coding: utf-8 -*-
"""MCP 测试计划工具单测：读工具 + preview → confirm 写工具。

运行: python manage.py test apps.mcp.tests.test_tools_test_plans
"""
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from mcp.server.fastmcp.exceptions import ToolError

from apps.executions.models import TestPlan, TestRun, TestRunCase
from apps.mcp import tools as mcp_tools
from apps.mcp.models import McpCallLog, McpPendingConfirm
from apps.mcp.tests.test_tools import ToolBase
from apps.mcp.tests.utils import ctx_with_jwt
from apps.projects.models import Project
from apps.testcases.models import TestCase as FunctionalTestCase
from apps.versions.models import Version


class PlanToolBase(ToolBase):
    def setUp(self):
        super().setUp()
        self.case_a = FunctionalTestCase.objects.create(
            project=self.project, title='用例 A', author=self.user)
        self.case_b = FunctionalTestCase.objects.create(
            project=self.project, title='用例 B', author=self.user)
        self.other_ctx, _ = ctx_with_jwt(self.other)

    def create_plan(self, ctx=None, **data):
        payload = {'name': '回归计划', 'projects': [self.project.id],
                   'testcases': [self.case_a.id, self.case_b.id]}
        payload.update(data)
        ctx = ctx or self.ctx
        preview = mcp_tools.preview_create_test_plan(ctx, data=payload)
        return mcp_tools.confirm_create_test_plan(ctx, preview['confirm_token'])

    def update_plan(self, plan_id, data, ctx=None):
        ctx = ctx or self.ctx
        preview = mcp_tools.preview_update_test_plan(ctx, plan_id=plan_id, data=data)
        return preview, mcp_tools.confirm_update_test_plan(ctx, preview['confirm_token'])

    def plan_case_ids(self, plan_id):
        return sorted(TestRunCase.objects.filter(
            test_run__test_plan_id=plan_id).values_list('testcase_id', flat=True))

    def mark(self, plan_id, testcase, status):
        TestRunCase.objects.filter(
            test_run__test_plan_id=plan_id, testcase=testcase).update(status=status)

    def case_status(self, plan_id, testcase):
        return TestRunCase.objects.get(test_run__test_plan_id=plan_id, testcase=testcase).status


class CreateTestPlanToolTest(PlanToolBase):
    def test_preview_does_not_write_and_confirm_creates_plan(self):
        preview = mcp_tools.preview_create_test_plan(self.ctx, data={
            'name': '版本回归计划', 'description': 'MCP 创建',
            'projects': [self.project.id],
            'testcases': [self.case_a.id, self.case_b.id],
            'assignees': [self.other.id],
        })
        self.assertIn('版本回归计划', preview['preview'])
        self.assertIn('2 条用例', preview['preview'])
        self.assertFalse(TestPlan.objects.exists())

        result = mcp_tools.confirm_create_test_plan(self.ctx, preview['confirm_token'])

        plan = TestPlan.objects.get(id=result['plan_id'])
        self.assertEqual((plan.name, plan.description), ('版本回归计划', 'MCP 创建'))
        self.assertEqual(plan.creator_id, self.user.id)
        self.assertEqual(result['testcase_count'], 2)
        self.assertEqual(
            result['test_run_ids'],
            list(TestRun.objects.filter(test_plan=plan).values_list('id', flat=True)))
        # 恰好一位指派人时，该人是新增执行用例的默认执行人
        self.assertEqual(
            set(TestRunCase.objects.filter(test_run__test_plan=plan).values_list(
                'executed_by_id', flat=True)),
            {self.other.id})

    def test_invalid_arguments_fail_at_preview_without_pending_record(self):
        foreign = Project.objects.create(name='别人项目', owner=self.other)
        foreign_case = FunctionalTestCase.objects.create(
            project=foreign, title='别人项目的用例', author=self.other)
        invalid_payloads = [
            {'name': '越权计划', 'projects': [foreign.id]},
            {'name': '没有项目', 'projects': []},
            {'name': '', 'projects': [self.project.id]},
            {'name': '串项目用例', 'projects': [self.project.id], 'testcases': [foreign_case.id]},
            {'name': '未知指派人', 'projects': [self.project.id], 'assignees': [987654]},
            {'name': '未知版本', 'projects': [self.project.id], 'version': 987654},
            {'name': '未知字段', 'projects': [self.project.id], 'created_at': '2001-01-01'},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ToolError):
                mcp_tools.preview_create_test_plan(self.ctx, data=payload)

        self.assertFalse(McpPendingConfirm.objects.exists())
        self.assertFalse(TestPlan.objects.exists())
        # 被拒绝的写入同样留下审计日志
        self.assertEqual(
            McpCallLog.objects.filter(
                tool_name='preview_create_test_plan', status='error').count(),
            len(invalid_payloads))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testhub.example.com'])
    def test_create_does_not_depend_on_request_host(self):
        # 创建人有头像、生产 ALLOWED_HOSTS 为具体域名时也不能失败
        self.user.avatar = 'avatars/a.png'
        self.user.save()

        result = self.create_plan()

        self.assertEqual(TestPlan.objects.filter(id=result['plan_id']).count(), 1)

    def test_write_invalidates_list_cache(self):
        self.assertEqual(mcp_tools.list_test_plans(self.ctx)['total'], 0)

        self.create_plan()

        self.assertEqual(mcp_tools.list_test_plans(self.ctx)['total'], 1)


class UpdateTestPlanToolTest(PlanToolBase):
    def test_fields_only_update_keeps_runs_and_is_allowed_for_assignee(self):
        plan_id = self.create_plan(assignees=[self.other.id])['plan_id']
        self.mark(plan_id, self.case_a, 'passed')

        # 被指派人不是项目成员，但只改基础字段不涉及任何项目
        _, result = self.update_plan(
            plan_id, {'name': '回归计划-v2', 'is_active': False}, ctx=self.other_ctx)

        plan = TestPlan.objects.get(id=plan_id)
        self.assertEqual((plan.name, plan.is_active), ('回归计划-v2', False))
        self.assertEqual(result['updated_fields'], ['is_active', 'name'])
        self.assertEqual(result['testcases_removed'], 0)
        self.assertEqual(self.case_status(plan_id, self.case_a), 'passed')
        self.assertEqual(self.plan_case_ids(plan_id), [self.case_a.id, self.case_b.id])

    def test_empty_or_null_relations_are_rejected(self):
        plan_id = self.create_plan()['plan_id']

        for data in ({'projects': []}, {'projects': None}, {'testcases': None},
                     {'name': ''}, {'is_active': 'yes'}, {}):
            with self.subTest(data=data), self.assertRaises(ToolError):
                mcp_tools.preview_update_test_plan(self.ctx, plan_id=plan_id, data=data)

        self.assertEqual(self.plan_case_ids(plan_id), [self.case_a.id, self.case_b.id])

    def test_add_and_remove_testcases_keep_other_results(self):
        plan_id = self.create_plan()['plan_id']
        self.mark(plan_id, self.case_a, 'passed')
        case_c = FunctionalTestCase.objects.create(
            project=self.project, title='用例 C', author=self.user)

        _, result = self.update_plan(
            plan_id, {'add_testcases': [case_c.id], 'remove_testcases': [self.case_b.id]})

        self.assertEqual(self.plan_case_ids(plan_id), [self.case_a.id, case_c.id])
        self.assertEqual(self.case_status(plan_id, self.case_a), 'passed')
        self.assertEqual((result['testcases_added'], result['testcases_removed']), (1, 1))
        self.assertEqual(result['results_removed'], 0)

    def test_full_replacement_preview_states_result_loss(self):
        plan_id = self.create_plan()['plan_id']
        self.mark(plan_id, self.case_a, 'passed')
        case_c = FunctionalTestCase.objects.create(
            project=self.project, title='用例 C', author=self.user)

        preview, result = self.update_plan(plan_id, {'testcases': [case_c.id]})

        self.assertIn('移除 2 条执行用例', preview['preview'])
        self.assertIn('1 条已有执行结果', preview['preview'])
        self.assertEqual(self.plan_case_ids(plan_id), [case_c.id])
        self.assertEqual(result['results_removed'], 1)

    def test_testcases_cannot_mix_replacement_and_incremental(self):
        plan_id = self.create_plan()['plan_id']

        with self.assertRaises(ToolError):
            mcp_tools.preview_update_test_plan(self.ctx, plan_id=plan_id, data={
                'testcases': [self.case_a.id], 'add_testcases': [self.case_b.id]})

    def test_partial_member_cannot_drop_other_projects_run(self):
        second = Project.objects.create(name='第二个项目', owner=self.user)
        plan_id = self.create_plan(projects=[self.project.id, second.id])['plan_id']
        self.project.members.add(self.other)  # 只是第一个项目的成员

        with self.assertRaises(ToolError):
            mcp_tools.preview_update_test_plan(
                self.other_ctx, plan_id=plan_id, data={'projects': [self.project.id]})

        self.assertTrue(TestRun.objects.filter(test_plan_id=plan_id, project=second).exists())

    def test_removing_project_drops_its_run_for_authorized_user(self):
        second = Project.objects.create(name='第二个项目', owner=self.user)
        plan_id = self.create_plan(projects=[self.project.id, second.id])['plan_id']

        preview, result = self.update_plan(plan_id, {'projects': [self.project.id]})

        self.assertIn('第二个项目', preview['preview'])
        self.assertEqual(result['projects_removed'], [second.id])
        self.assertFalse(TestRun.objects.filter(test_plan_id=plan_id, project=second).exists())
        self.assertEqual(self.plan_case_ids(plan_id), [self.case_a.id, self.case_b.id])

    def test_unknown_version_is_rejected_and_null_clears(self):
        version = Version.objects.create(name='v1.0', created_by=self.user)
        version.projects.add(self.project)
        plan_id = self.create_plan(version=version.id)['plan_id']

        with self.assertRaises(ToolError):
            mcp_tools.preview_update_test_plan(
                self.ctx, plan_id=plan_id, data={'version': 987654})
        self.assertEqual(TestPlan.objects.get(id=plan_id).version_id, version.id)

        self.update_plan(plan_id, {'version': None})
        self.assertIsNone(TestPlan.objects.get(id=plan_id).version_id)

    def test_invisible_plan_is_rejected(self):
        plan_id = self.create_plan()['plan_id']

        with self.assertRaises(ToolError):
            mcp_tools.preview_update_test_plan(
                self.other_ctx, plan_id=plan_id, data={'name': '越权修改'})

    @override_settings(MCP_HUMAN_APPROVAL=True)
    def test_human_approval_holds_the_write(self):
        with override_settings(MCP_HUMAN_APPROVAL=False):
            plan_id = self.create_plan()['plan_id']

        preview = mcp_tools.preview_update_test_plan(
            self.ctx, plan_id=plan_id, data={'testcases': []})
        result = mcp_tools.confirm_update_test_plan(self.ctx, preview['confirm_token'])

        self.assertEqual(result['status'], 'awaiting_approval')
        self.assertEqual(self.plan_case_ids(plan_id), [self.case_a.id, self.case_b.id])
        self.assertTrue(McpPendingConfirm.objects.filter(
            tool_name='confirm_update_test_plan', awaiting_human=True).exists())


class ReadTestPlanToolTest(PlanToolBase):
    def test_list_filters_and_visibility(self):
        active_id = self.create_plan(name='启用的计划')['plan_id']
        closed_id = self.create_plan(name='关闭的计划', is_active=False)['plan_id']

        everything = mcp_tools.list_test_plans(self.ctx)
        active = mcp_tools.list_test_plans(self.ctx, is_active=True)
        closed = mcp_tools.list_test_plans(self.ctx, is_active=False, keyword='关闭')

        self.assertEqual(everything['total'], 2)
        self.assertEqual([item['id'] for item in active['items']], [active_id])
        self.assertEqual([item['id'] for item in closed['items']], [closed_id])
        self.assertEqual(active['items'][0]['testcase_count'], 2)
        self.assertEqual(mcp_tools.list_test_plans(self.other_ctx)['total'], 0)

    def test_get_plan_paginates_run_cases_with_bounded_queries(self):
        cases = [FunctionalTestCase.objects.create(
            project=self.project, title=f'批量用例 {index}', author=self.user)
            for index in range(12)]
        plan_id = self.create_plan(testcases=[case.id for case in cases])['plan_id']
        self.mark(plan_id, cases[0], 'passed')
        self.mark(plan_id, cases[1], 'failed')

        with CaptureQueriesContext(connection) as queries:
            detail = mcp_tools.get_test_plan(self.ctx, plan_id=plan_id, case_limit=5)

        self.assertEqual(detail['run_cases']['total'], 12)
        self.assertEqual(len(detail['run_cases']['items']), 5)
        self.assertEqual(detail['run_cases']['items'][0]['testcase_id'], cases[0].id)
        self.assertEqual(detail['run_cases']['items'][0]['status'], 'passed')
        self.assertEqual(detail['projects'], [{'id': self.project.id, 'name': self.project.name}])
        self.assertEqual(detail['progress']['total'], 12)
        self.assertEqual(detail['progress']['tested'], 2)
        self.assertEqual(detail['test_runs'][0]['progress']['failed'], 1)
        # 查询数与执行用例数无关：鉴权 + 计划 + 执行记录/进度 + 用例分页 + 关联 + 调用日志
        self.assertLessEqual(len(queries), 14)

        second_page = mcp_tools.get_test_plan(
            self.ctx, plan_id=plan_id, case_limit=5, case_offset=10)
        self.assertEqual(len(second_page['run_cases']['items']), 2)

    def test_get_plan_requires_visibility(self):
        plan_id = self.create_plan()['plan_id']

        with self.assertRaises(ToolError):
            mcp_tools.get_test_plan(self.other_ctx, plan_id=plan_id)
