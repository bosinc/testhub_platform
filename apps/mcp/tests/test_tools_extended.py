# -*- coding: utf-8 -*-
"""MCP 新增能力单测：元素库 / 场景·套件列表 / 创建关联 / 更新 / 执行结果查询。

运行: python manage.py test apps.mcp.tests.test_tools_extended
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.api_testing.models import (
    ApiCollection, ApiProject, ApiRequest, ApiSuiteExecution, ScenarioCase,
    ScenarioCaseRequest, TestExecution as ApiExecution, TestSuite as ApiSuite,
    TestSuiteItem,
)
from apps.mcp import actions as mcp_actions
from apps.mcp import tools as mcp_tools
from apps.projects.models import Project
from apps.testcases.models import TestCase as FunctionalTestCase, TestCaseAutomationLink
from apps.ui_automation.models import (
    Element, ElementGroup, LocatorStrategy, TestCase as UiCase, TestCaseExecution,
    TestCaseStep, TestSuite as UiSuite, TestSuiteTestCase, TestExecution as UiExecution,
)
from apps.mcp.tests.utils import ctx_with_jwt

from mcp.server.fastmcp.exceptions import ToolError

User = get_user_model()


def _create_ui_project(user, project, name):
    from apps.ui_automation.models import UiProject
    return UiProject.objects.create(
        name=name, base_url='https://example.com', owner=user, workspace_project=project)


def _create_api_project(user, project, name):
    return ApiProject.objects.create(
        name=name, project_type='HTTP', status='IN_PROGRESS',
        owner=user, workspace_project=project)


class BaseFixture(TestCase):
    """统一构造：统一项目 + 关联的 UI/API 自动化项目。"""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create(username='mcp_ext_user', email='ext@a.com')
        self.other = User.objects.create(username='mcp_ext_other', email='ext@b.com')
        self.project = Project.objects.create(name='统一项目', owner=self.user)
        self.ui_project = _create_ui_project(self.user, self.project, 'UI 项目')
        self.api_project = _create_api_project(self.user, self.project, 'API 项目')
        self.ctx, _ = ctx_with_jwt(self.user)

    def tearDown(self):
        cache.clear()


class ElementLibraryToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        self.strategy = LocatorStrategy.objects.create(name='CSS')
        self.group = ElementGroup.objects.create(
            name='登录页', project=self.ui_project)
        self.element = Element.objects.create(
            project=self.ui_project, group=self.group, name='登录按钮',
            element_type='BUTTON', locator_strategy=self.strategy,
            locator_value='#login', page='登录页', created_by=self.user)
        Element.objects.create(
            project=self.ui_project, name='页脚文本', element_type='TEXT',
            locator_strategy=self.strategy, locator_value='.footer', page='公共组件')

    def test_returns_page_modules_and_elements(self):
        result = mcp_tools.list_ui_elements(self.ctx, project_id=self.ui_project.id)
        self.assertEqual(result['total'], 2)
        modules = {m['name']: m for m in result['page_modules']}
        self.assertIn('登录页', modules)
        self.assertEqual(modules['登录页']['element_count'], 1)
        self.assertEqual(modules['登录页']['source'], 'group')
        # 未归组元素按 page 字段聚合成页面模块
        self.assertIn('公共组件', modules)
        self.assertEqual(modules['公共组件']['source'], 'page')

        item = next(i for i in result['items'] if i['name'] == '登录按钮')
        self.assertEqual(item['locator_strategy'], 'CSS')
        self.assertEqual(item['locator_value'], '#login')
        self.assertEqual(item['group_name'], '登录页')

    def test_filter_by_page_and_keyword(self):
        by_page = mcp_tools.list_ui_elements(
            self.ctx, project_id=self.ui_project.id, page='公共组件')
        self.assertEqual(by_page['total'], 1)
        self.assertEqual(by_page['items'][0]['name'], '页脚文本')

        by_keyword = mcp_tools.list_ui_elements(
            self.ctx, project_id=self.ui_project.id, keyword='登录')
        self.assertEqual(by_keyword['total'], 1)
        self.assertEqual(by_keyword['items'][0]['id'], self.element.id)

    def test_permission_denied_for_foreign_project(self):
        foreign = _create_ui_project(self.other, None, '别人的 UI 项目')
        with self.assertRaises(ToolError):
            mcp_tools.list_ui_elements(self.ctx, project_id=foreign.id)


class ApiListToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        collection = ApiCollection.objects.create(name='用户', project=self.api_project)
        self.request = ApiRequest.objects.create(
            collection=collection, name='登录', method='POST',
            url='https://api.example.com/login', created_by=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.api_project, name='登录场景', created_by=self.user)
        ScenarioCaseRequest.objects.create(
            scenario_case=self.scenario, request=self.request)
        self.suite = ApiSuite.objects.create(
            project=self.api_project, name='冒烟套件', created_by=self.user)
        TestSuiteItem.objects.create(test_suite=self.suite, scenario_case=self.scenario)

    def test_list_api_scenarios_with_request_count(self):
        result = mcp_tools.list_api_scenarios(self.ctx, project_id=self.api_project.id)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['request_count'], 1)

    def test_list_api_suites_with_scenario_count(self):
        result = mcp_tools.list_api_suites(self.ctx, project_id=self.api_project.id)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['scenario_count'], 1)

    def test_list_ui_suites_with_case_count(self):
        suite = UiSuite.objects.create(project=self.ui_project, name='UI 冒烟')
        case = UiCase.objects.create(
            name='登录', project=self.ui_project, created_by=self.user)
        TestSuiteTestCase.objects.create(test_suite=suite, test_case=case)
        result = mcp_tools.list_ui_suites(self.ctx, project_id=self.ui_project.id)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['case_count'], 1)


class CreateUiCaseToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        self.strategy = LocatorStrategy.objects.create(name='CSS')
        self.element = Element.objects.create(
            project=self.ui_project, name='用户名输入框', element_type='INPUT',
            locator_strategy=self.strategy, locator_value='#username', created_by=self.user)
        self.formal_case = FunctionalTestCase.objects.create(
            project=self.project, title='登录功能',
            author=self.user)

    def _create(self, replace_link=False, **overrides):
        data = {
            'name': '登录-正常登录',
            'priority': 'high',
            'status': 'ready',
            'steps': [
                {'action_type': 'fill', 'element_id': self.element.id,
                 'input_value': 'admin'},
            ],
        }
        data.update(overrides)
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id, data=data,
            test_case_id=self.formal_case.id, replace_link=replace_link)
        return mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])

    def test_creates_case_steps_and_link(self):
        result = self._create()
        case = UiCase.objects.get(id=result['case_id'])
        self.assertEqual(case.name, '登录-正常登录')
        self.assertEqual(case.priority, 'high')
        self.assertEqual(case.steps.count(), 1)
        step = case.steps.first()
        self.assertEqual(step.action_type, 'fill')
        self.assertEqual(step.element_id, self.element.id)

        link = TestCaseAutomationLink.objects.get(test_case=self.formal_case)
        self.assertEqual(link.case_type, 'ui_test_case')
        self.assertEqual(link.object_id, case.id)
        self.assertFalse(result['automation_link']['replaced'])

    def test_unverified_element_returns_warning(self):
        """元素定位器未验证时要透出 warnings，而不是静默成功。"""
        self.assertEqual(self.element.validation_status, 'UNKNOWN')
        result = self._create()
        self.assertIn('warnings', result)
        self.assertIn('定位器未验证', result['warnings'][0])

    def test_valid_element_has_no_warning(self):
        self.element.validation_status = 'VALID'
        self.element.save(update_fields=['validation_status'])
        result = self._create()
        self.assertNotIn('warnings', result)

    def test_relink_rejected_without_replace_link(self):
        """已有关联时默认拒绝，避免新建用例后静默顶掉原链接。"""
        first = self._create()
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '登录-异常登录'}, test_case_id=self.formal_case.id)
        # 预览阶段就要提示会被拒绝
        self.assertIn('未传 replace_link=true 时确认会被拒绝', preview['preview'])
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])
        self.assertIn('已关联 UI 自动化用例', str(ctx.exception))
        # 拒绝后既不能顶掉原关联，也不能留下孤儿用例
        link = TestCaseAutomationLink.objects.get(test_case=self.formal_case)
        self.assertEqual(link.object_id, first['case_id'])
        self.assertFalse(UiCase.objects.filter(name='登录-异常登录').exists())

    def test_relink_replaces_when_replace_link_true(self):
        first = self._create()
        second = self._create(replace_link=True, name='登录-异常登录')
        link = TestCaseAutomationLink.objects.get(test_case=self.formal_case)
        self.assertEqual(link.object_id, second['case_id'])
        self.assertTrue(second['automation_link']['replaced'])
        self.assertNotEqual(first['case_id'], second['case_id'])
        self.assertEqual(
            TestCaseAutomationLink.objects.filter(test_case=self.formal_case).count(), 1)

    def test_preview_notes_replace_link(self):
        first = self._create()
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '登录-异常登录'}, test_case_id=self.formal_case.id,
            replace_link=True)
        self.assertIn('并替换功能用例', preview['preview'])
        self.assertIn(f'object_id={first["case_id"]}', preview['preview'])

    def test_rejects_element_from_other_project(self):
        foreign_project = _create_ui_project(self.other, None, '别人的 UI 项目')
        foreign_element = Element.objects.create(
            project=foreign_project, name='外部元素', element_type='BUTTON',
            locator_strategy=self.strategy, locator_value='#x', created_by=self.other)
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '非法步骤', 'steps': [
                {'action_type': 'click', 'element_id': foreign_element.id}]})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])
        self.assertFalse(UiCase.objects.filter(name='非法步骤').exists())

    def test_rejects_link_to_other_project_case(self):
        other_project = Project.objects.create(name='另一个统一项目', owner=self.user)
        other_case = FunctionalTestCase.objects.create(
            project=other_project, title='别人项目的用例', author=self.user)
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '跨项目关联'},
            test_case_id=other_case.id)
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])

    def test_link_failure_leaves_no_orphan_case(self):
        """关联校验失败必须整体回滚，不能留下孤立用例。"""
        other_project = Project.objects.create(name='另一个统一项目', owner=self.user)
        other_case = FunctionalTestCase.objects.create(
            project=other_project, title='别人项目的用例', author=self.user)
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '不应存在的用例', 'steps': [
                {'action_type': 'fill', 'element_id': self.element.id,
                 'input_value': 'admin'}]},
            test_case_id=other_case.id)
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])
        self.assertFalse(UiCase.objects.filter(name='不应存在的用例').exists())
        self.assertEqual(TestCaseStep.objects.count(), 0)


class CreateApiScenarioCaseToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        collection = ApiCollection.objects.create(name='订单', project=self.api_project)
        self.request = ApiRequest.objects.create(
            collection=collection, name='创建订单', method='POST',
            url='https://api.example.com/orders', created_by=self.user,
            assertions=[{'type': 'status_code', 'expected': 200}])
        self.formal_case = FunctionalTestCase.objects.create(
            project=self.project, title='下单功能',
            author=self.user)

    def test_creates_scenario_with_requests_and_link(self):
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '下单主流程',
                  'requests': [{'request_id': self.request.id, 'order': 0}]},
            test_case_id=self.formal_case.id)
        result = mcp_tools.confirm_create_api_scenario_case(
            self.ctx, preview['confirm_token'])

        scenario = ScenarioCase.objects.get(id=result['scenario_case_id'])
        self.assertEqual(scenario.name, '下单主流程')
        items = list(ScenarioCaseRequest.objects.filter(scenario_case=scenario))
        self.assertEqual(len(items), 1)
        # 未显式提供断言时沿用接口自身配置
        self.assertEqual(items[0].assertions[0]['type'], 'status_code')

        link = TestCaseAutomationLink.objects.get(test_case=self.formal_case)
        self.assertEqual(link.case_type, 'api_scenario_case')
        self.assertEqual(link.object_id, scenario.id)

    def test_rejects_request_from_other_project(self):
        foreign_project = _create_api_project(self.other, None, '别人的 API 项目')
        foreign_collection = ApiCollection.objects.create(
            name='外部', project=foreign_project)
        foreign_request = ApiRequest.objects.create(
            collection=foreign_collection, name='外部接口', method='GET',
            url='https://x.com', created_by=self.other)
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '非法场景', 'requests': [{'request_id': foreign_request.id}]})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_api_scenario_case(self.ctx, preview['confirm_token'])

    def test_rejects_empty_requests(self):
        """空场景能建但永远跑不了，创建阶段就要拒绝。"""
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id, data={'name': '空场景'})
        self.assertIn('无启用请求将被拒绝', preview['preview'])
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_api_scenario_case(self.ctx, preview['confirm_token'])
        self.assertIn('至少需要 1 个启用请求', str(ctx.exception))
        self.assertFalse(ScenarioCase.objects.filter(name='空场景').exists())

    def test_rejects_all_disabled_requests(self):
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '全禁用场景',
                  'requests': [{'request_id': self.request.id, 'enabled': False}]})
        self.assertIn('无启用请求将被拒绝', preview['preview'])
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_api_scenario_case(self.ctx, preview['confirm_token'])
        self.assertFalse(ScenarioCase.objects.filter(name='全禁用场景').exists())

    def test_preview_counts_enabled_requests(self):
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '混合场景',
                  'requests': [{'request_id': self.request.id},
                               {'request_id': self.request.id, 'enabled': False}]})
        self.assertIn('2 个请求，其中 1 个启用', preview['preview'])

    def test_relink_requires_replace_link(self):
        """已有关联时默认拒绝，且不留孤儿场景用例。"""
        data = {'name': '下单主流程',
                'requests': [{'request_id': self.request.id, 'order': 0}]}
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id, data=data,
            test_case_id=self.formal_case.id)
        first = mcp_tools.confirm_create_api_scenario_case(
            self.ctx, preview['confirm_token'])

        second_preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '下单-异常流程',
                  'requests': [{'request_id': self.request.id}]},
            test_case_id=self.formal_case.id)
        self.assertIn('未传 replace_link=true 时确认会被拒绝', second_preview['preview'])
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_api_scenario_case(
                self.ctx, second_preview['confirm_token'])
        link = TestCaseAutomationLink.objects.get(test_case=self.formal_case)
        self.assertEqual(link.object_id, first['scenario_case_id'])
        self.assertFalse(ScenarioCase.objects.filter(name='下单-异常流程').exists())

    def test_relink_replaces_when_replace_link_true(self):
        first = mcp_tools.confirm_create_api_scenario_case(
            self.ctx,
            mcp_tools.preview_create_api_scenario_case(
                self.ctx, project_id=self.api_project.id,
                data={'name': '下单主流程',
                      'requests': [{'request_id': self.request.id}]},
                test_case_id=self.formal_case.id)['confirm_token'])
        second = mcp_tools.confirm_create_api_scenario_case(
            self.ctx,
            mcp_tools.preview_create_api_scenario_case(
                self.ctx, project_id=self.api_project.id,
                data={'name': '下单-异常流程',
                      'requests': [{'request_id': self.request.id}]},
                test_case_id=self.formal_case.id, replace_link=True)['confirm_token'])
        link = TestCaseAutomationLink.objects.get(test_case=self.formal_case)
        self.assertEqual(link.object_id, second['scenario_case_id'])
        self.assertTrue(second['automation_link']['replaced'])
        self.assertNotEqual(first['scenario_case_id'], second['scenario_case_id'])

    def test_link_failure_leaves_no_orphan_scenario(self):
        """关联校验失败必须整体回滚，不能留下孤立场景用例与编排。"""
        other_project = Project.objects.create(name='另一个统一项目', owner=self.user)
        other_case = FunctionalTestCase.objects.create(
            project=other_project, title='别人项目的用例', author=self.user)
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '不应存在的场景',
                  'requests': [{'request_id': self.request.id}]},
            test_case_id=other_case.id)
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_api_scenario_case(self.ctx, preview['confirm_token'])
        self.assertFalse(ScenarioCase.objects.filter(name='不应存在的场景').exists())
        self.assertEqual(ScenarioCaseRequest.objects.count(), 0)


class CreateTestcaseToolTest(BaseFixture):
    """创建功能用例：在指定模块目录下创建、未归模块与跨项目模块校验。"""

    def setUp(self):
        super().setUp()
        from apps.testcases.models import TestCaseModule
        self.login_module = TestCaseModule.objects.create(project=self.project, name='登录')
        self.child_module = TestCaseModule.objects.create(
            project=self.project, name='短信登录', parent=self.login_module)

    def _create(self, **data):
        payload = {'title': '登录成功'}
        payload.update(data)
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id, data=payload)
        return mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])

    def test_creates_case_in_specified_module(self):
        """data.module_id 指定后用例落到该模块（含子模块目录）。"""
        result = self._create(module_id=self.child_module.id)
        case = FunctionalTestCase.objects.get(id=result['testcase_id'])
        self.assertEqual(case.module_id, self.child_module.id)
        self.assertEqual(result['module_id'], self.child_module.id)

    def test_creates_case_without_module(self):
        result = self._create()
        self.assertIsNone(result['module_id'])
        self.assertIsNone(
            FunctionalTestCase.objects.get(id=result['testcase_id']).module_id)

    def test_module_id_zero_means_unassigned(self):
        result = self._create(module_id=0)
        self.assertIsNone(result['module_id'])

    def test_ignores_step_ids_on_create(self):
        """新建用例时 steps 带 id 没有意义：忽略并回显，而不是报错。"""
        result = self._create(steps=[
            {'id': 999999, 'action': '第一步', 'expected': '预期一'},
            {'id': 888888, 'action': '第二步', 'expected': '预期二'},
        ])
        case = FunctionalTestCase.objects.get(id=result['testcase_id'])
        self.assertEqual(result['steps_created'], 2)
        self.assertEqual(result['steps_ignored_ids'], [999999, 888888])
        self.assertEqual(
            list(case.step_details.values_list('action', flat=True)),
            ['第一步', '第二步'])
        # 落库的是数据库分配的主键，不是请求里带的 id
        self.assertFalse(case.step_details.filter(id__in=[999999, 888888]).exists())

    def test_create_ignores_duplicate_step_ids(self):
        """新建时 id 会被丢弃，重复 id 不应阻断创建。"""
        result = self._create(steps=[
            {'id': 777, 'action': '第一步', 'expected': ''},
            {'id': 777, 'action': '第二步', 'expected': ''},
        ])
        case = FunctionalTestCase.objects.get(id=result['testcase_id'])
        self.assertEqual(result['steps_created'], 2)
        self.assertEqual(result['steps_ignored_ids'], [777])
        self.assertEqual(case.step_details.count(), 2)

    def test_rejects_module_from_other_project(self):
        """模块必须属于同一项目，跨项目模块整体拒绝且不落库。"""
        from apps.testcases.models import TestCaseModule
        other_project = Project.objects.create(name='另一个统一项目', owner=self.user)
        foreign = TestCaseModule.objects.create(project=other_project, name='外部模块')
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '跨项目模块用例', 'module_id': foreign.id})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertIn('不存在或不属于当前项目', str(ctx.exception))
        self.assertFalse(
            FunctionalTestCase.objects.filter(title='跨项目模块用例').exists())

    def test_missing_module_rejected(self):
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '不存在模块用例', 'module_id': 999999})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertFalse(
            FunctionalTestCase.objects.filter(title='不存在模块用例').exists())


class UpdateTestcaseToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        from apps.testcases.models import TestCaseModule
        self.module = TestCaseModule.objects.create(project=self.project, name='登录模块')
        self.case = FunctionalTestCase.objects.create(
            project=self.project, title='登录', author=self.user)

    def test_updates_fields_with_project_priority(self):
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'priority': 'high', 'status': 'active', 'tags': ['回归', '登录'],
                  'module_id': self.module.id, 'notes': 'AI 生成',
                  'steps': [{'action': '输入账号', 'expected': '输入成功'}]})
        result = mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])

        self.case.refresh_from_db()
        self.assertEqual(self.case.priority, 'high')
        self.assertEqual(self.case.status, 'active')
        self.assertEqual(self.case.tags, ['回归', '登录'])
        self.assertEqual(self.case.module_id, self.module.id)
        self.assertEqual(self.case.notes, 'AI 生成')
        self.assertIn('输入账号', [step.action for step in self.case.step_details.all()])
        self.assertIn('priority', result['updated_fields'])

    def test_read_then_update_keeps_step_identity(self):
        """get_testcase 返回的步骤 id 回传后走增量更新，主键不变。"""
        from apps.testcases.models import TestCaseStep
        step = TestCaseStep.objects.create(
            testcase=self.case, sort_order=0, action='原步骤', expected='原预期')

        detail = mcp_tools.get_testcase(self.ctx, testcase_id=self.case.id)
        returned = next(item for item in detail['steps'] if item['id'] == step.id)

        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'steps': [{'id': returned['id'], 'sort_order': 0,
                             'action': '改后', 'expected': '改后预期'}]})
        result = mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])

        self.assertEqual(result['steps_updated'], 1)
        step.refresh_from_db()
        self.assertEqual((step.action, step.expected), ('改后', '改后预期'))

    def test_step_details_replaced(self):
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'steps': [{'action': '点击登录', 'expected': '跳转首页'}]})
        result = mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        self.assertEqual(result['steps_replaced'], 1)
        self.assertEqual(self.case.step_details.count(), 1)
        self.assertEqual(self.case.step_details.first().action, '点击登录')

    def test_update_step_by_id_keeps_identity(self):
        """带 id 的步骤原地更新，主键不变。"""
        from apps.testcases.models import TestCaseStep
        TestCaseStep.objects.create(
            testcase=self.case, sort_order=0, action='旧步骤', expected='旧预期')
        step_id = self.case.step_details.first().id

        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'steps': [{'id': step_id, 'sort_order': 0,
                             'action': '新步骤', 'expected': '新预期'}]})
        result = mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])

        self.assertEqual(result['steps_updated'], 1)
        self.assertEqual(result['steps_replaced'], 1)
        self.assertEqual(self.case.step_details.count(), 1)
        step = self.case.step_details.first()
        self.assertEqual(step.id, step_id)
        self.assertEqual((step.action, step.expected), ('新步骤', '新预期'))

    def test_foreign_step_id_rejected(self):
        """不能借更新接口改写别的用例的步骤。"""
        from apps.testcases.models import TestCaseStep
        other = FunctionalTestCase.objects.create(
            project=self.project, title='别的用例', author=self.user)
        foreign = TestCaseStep.objects.create(
            testcase=other, sort_order=0, action='别人的步骤', expected='')

        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'steps': [{'id': foreign.id, 'action': '篡改', 'expected': ''}]})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        foreign.refresh_from_db()
        self.assertEqual(foreign.action, '别人的步骤')

    def test_duplicate_step_id_rejected(self):
        """同一条步骤提交两次必须报错，不能静默覆盖。"""
        from apps.testcases.models import TestCaseStep
        step = TestCaseStep.objects.create(
            testcase=self.case, sort_order=0, action='原步骤', expected='')

        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'steps': [{'id': step.id, 'action': '覆盖一', 'expected': ''},
                            {'id': step.id, 'action': '覆盖二', 'expected': ''}]})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        self.assertIn('重复', str(ctx.exception))
        step.refresh_from_db()
        self.assertEqual(step.action, '原步骤')

    def test_p0_alias_rejected(self):
        """优先级只认项目枚举，P0-P3 写法必须报错。"""
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id, data={'priority': 'P1'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        self.assertIn('low, medium, high, critical', str(ctx.exception))
        self.case.refresh_from_db()
        self.assertEqual(self.case.priority, 'medium')

    def test_invalid_step_details_rolls_back_fields(self):
        """结构化步骤非法时，同批字段更新必须回滚。"""
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'title': '新标题', 'steps': ['不是对象']})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        self.case.refresh_from_db()
        self.assertEqual(self.case.title, '登录')

    def test_over_length_step_rejected(self):
        """MCP 与 REST 共用 2000 字符上限，不能绕过序列化器写超长步骤。"""
        from apps.testcases.steps import STEP_TEXT_MAX_LENGTH
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=self.case.id,
            data={'steps': [{'action': 'a' * (STEP_TEXT_MAX_LENGTH + 1), 'expected': ''}]})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        self.assertIn(str(STEP_TEXT_MAX_LENGTH), str(ctx.exception))
        self.assertEqual(self.case.step_details.count(), 0)

    def test_permission_denied(self):
        foreign_project = Project.objects.create(name='别人项目', owner=self.other)
        foreign_case = FunctionalTestCase.objects.create(
            project=foreign_project, title='别人用例',
            author=self.other)
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=foreign_case.id, data={'title': '篡改'})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])


class UpdateUiCaseToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        self.strategy = LocatorStrategy.objects.create(name='CSS')
        self.element = Element.objects.create(
            project=self.ui_project, name='登录按钮', element_type='BUTTON',
            locator_strategy=self.strategy, locator_value='#login', created_by=self.user)
        self.case = UiCase.objects.create(
            name='旧名称', project=self.ui_project, created_by=self.user,
            priority='low')
        TestCaseStep.objects.create(
            test_case=self.case, step_number=1, action_type='click',
            element=self.element)

    def test_updates_fields_and_replaces_steps(self):
        preview = mcp_tools.preview_update_ui_case(
            self.ctx, case_id=self.case.id,
            data={'name': '新名称', 'priority': 'high', 'status': 'ready',
                  'steps': [{'action_type': 'hover', 'element_id': self.element.id}]})
        result = mcp_tools.confirm_update_ui_case(self.ctx, preview['confirm_token'])

        self.case.refresh_from_db()
        self.assertEqual(self.case.name, '新名称')
        self.assertEqual(self.case.priority, 'high')
        self.assertEqual(result['steps_replaced'], 1)
        self.assertEqual(self.case.steps.count(), 1)
        self.assertEqual(self.case.steps.first().action_type, 'hover')

    def test_invalid_status_rejected(self):
        preview = mcp_tools.preview_update_ui_case(
            self.ctx, case_id=self.case.id, data={'status': 'unknown'})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_ui_case(self.ctx, preview['confirm_token'])

    def test_invalid_steps_do_not_change_fields(self):
        """步骤校验失败时主记录字段与原步骤都必须保持不变。"""
        preview = mcp_tools.preview_update_ui_case(
            self.ctx, case_id=self.case.id,
            data={'name': '不应生效', 'steps': [
                {'action_type': 'click', 'element_id': 999999}]})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_ui_case(self.ctx, preview['confirm_token'])
        self.case.refresh_from_db()
        self.assertEqual(self.case.name, '旧名称')
        self.assertEqual(self.case.steps.count(), 1)
        self.assertEqual(self.case.steps.first().action_type, 'click')


class UpdateApiScenarioCaseToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        collection = ApiCollection.objects.create(name='订单', project=self.api_project)
        self.request_a = ApiRequest.objects.create(
            collection=collection, name='接口A', method='GET',
            url='https://api.example.com/a', created_by=self.user)
        self.request_b = ApiRequest.objects.create(
            collection=collection, name='接口B', method='GET',
            url='https://api.example.com/b', created_by=self.user)
        self.scenario = ScenarioCase.objects.create(
            project=self.api_project, name='旧场景', created_by=self.user)
        ScenarioCaseRequest.objects.create(
            scenario_case=self.scenario, request=self.request_a, order=0)

    def test_replaces_requests(self):
        preview = mcp_tools.preview_update_api_scenario_case(
            self.ctx, scenario_case_id=self.scenario.id,
            data={'name': '新场景', 'requests': [
                {'request_id': self.request_b.id},
                {'request_id': self.request_a.id}]})
        result = mcp_tools.confirm_update_api_scenario_case(
            self.ctx, preview['confirm_token'])

        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.name, '新场景')
        self.assertEqual(result['requests_replaced'], 2)
        ordered = list(self.scenario.scenariocaserequest_set.order_by('order')
                       .values_list('request_id', flat=True))
        self.assertEqual(ordered, [self.request_b.id, self.request_a.id])

    def test_invalid_request_rejected(self):
        preview = mcp_tools.preview_update_api_scenario_case(
            self.ctx, scenario_case_id=self.scenario.id,
            data={'requests': [{'request_id': 999999}]})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_api_scenario_case(self.ctx, preview['confirm_token'])
        # 校验失败时原编排保持不变
        self.assertEqual(self.scenario.scenariocaserequest_set.count(), 1)

    def test_duplicate_requests_rejected_and_fields_rollback(self):
        preview = mcp_tools.preview_update_api_scenario_case(
            self.ctx, scenario_case_id=self.scenario.id,
            data={'name': '不应生效', 'requests': [
                {'request_id': self.request_a.id},
                {'request_id': self.request_a.id}]})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_update_api_scenario_case(self.ctx, preview['confirm_token'])
        self.assertIn('重复', str(ctx.exception))
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.name, '旧场景')
        self.assertEqual(self.scenario.scenariocaserequest_set.count(), 1)

    def test_runnable_flag_false_when_all_requests_disabled(self):
        """整体覆盖成全禁用后必须报 runnable=false 并给出提示。"""
        preview = mcp_tools.preview_update_api_scenario_case(
            self.ctx, scenario_case_id=self.scenario.id,
            data={'requests': [{'request_id': self.request_a.id, 'enabled': False}]})
        result = mcp_tools.confirm_update_api_scenario_case(
            self.ctx, preview['confirm_token'])
        self.assertFalse(result['runnable'])
        self.assertIn('warning', result)
        self.assertIn('没有启用请求', result['warning'])

    def test_runnable_flag_true_with_enabled_request(self):
        preview = mcp_tools.preview_update_api_scenario_case(
            self.ctx, scenario_case_id=self.scenario.id,
            data={'requests': [{'request_id': self.request_a.id}]})
        result = mcp_tools.confirm_update_api_scenario_case(
            self.ctx, preview['confirm_token'])
        self.assertTrue(result['runnable'])
        self.assertNotIn('warning', result)


class UpdateUiElementToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        self.css = LocatorStrategy.objects.create(name='CSS')
        self.xpath = LocatorStrategy.objects.create(name='XPath')
        self.group = ElementGroup.objects.create(name='登录页', project=self.ui_project)
        self.element = Element.objects.create(
            project=self.ui_project, name='登录按钮', element_type='BUTTON',
            locator_strategy=self.css, locator_value='#login',
            created_by=self.user)

    def test_updates_locator_and_attributes(self):
        preview = mcp_tools.preview_update_ui_element(
            self.ctx, element_id=self.element.id,
            data={'locator_strategy': 'xpath', 'locator_value': '//button[@id="login"]',
                  'group_id': self.group.id, 'wait_timeout': 10,
                  'backup_locators': [{'strategy': 'CSS', 'value': '.login'}]})
        result = mcp_tools.confirm_update_ui_element(
            self.ctx, preview['confirm_token'])

        self.element.refresh_from_db()
        self.assertEqual(self.element.locator_strategy_id, self.xpath.id)
        self.assertEqual(self.element.locator_value, '//button[@id="login"]')
        self.assertEqual(self.element.group_id, self.group.id)
        self.assertEqual(self.element.wait_timeout, 10)
        self.assertEqual(result['locator_strategy'], 'XPath')

    def test_invalid_element_type_rejected(self):
        preview = mcp_tools.preview_update_ui_element(
            self.ctx, element_id=self.element.id, data={'element_type': 'UNKNOWN'})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_ui_element(self.ctx, preview['confirm_token'])

    def test_unknown_locator_strategy_rejected(self):
        preview = mcp_tools.preview_update_ui_element(
            self.ctx, element_id=self.element.id, data={'locator_strategy': '不存在'})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_update_ui_element(self.ctx, preview['confirm_token'])


class RunToolWiringTest(BaseFixture):
    """执行类工具：校验权限与参数，并把执行器返回结果透传给 Agent。"""

    def setUp(self):
        super().setUp()
        self.scenario = ScenarioCase.objects.create(
            project=self.api_project, name='登录场景', created_by=self.user)
        collection = ApiCollection.objects.create(name='登录', project=self.api_project)
        api_request = ApiRequest.objects.create(
            collection=collection, name='登录接口', method='POST',
            url='https://api.example.com/login', created_by=self.user)
        ScenarioCaseRequest.objects.create(
            scenario_case=self.scenario, request=api_request)
        self.api_suite = ApiSuite.objects.create(
            project=self.api_project, name='冒烟', created_by=self.user)
        self.ui_suite = UiSuite.objects.create(project=self.ui_project, name='UI 冒烟')

    def test_run_api_scenario_requires_enabled_requests(self):
        empty_scenario = ScenarioCase.objects.create(
            project=self.api_project, name='空场景', created_by=self.user)
        preview = mcp_tools.preview_run_api_scenario(
            self.ctx, scenario_case_id=empty_scenario.id)
        with self.assertRaises(ToolError):
            mcp_tools.confirm_run_api_scenario(self.ctx, preview['confirm_token'])

    def test_run_api_scenario_returns_execution_id(self):
        preview = mcp_tools.preview_run_api_scenario(
            self.ctx, scenario_case_id=self.scenario.id)
        self.assertIn('场景用例', preview['preview'])
        with patch('apps.api_testing.mcp_runner.start_scenario_execution') as starter:
            starter.return_value = ApiExecution(
                id=99, scenario_case=self.scenario, status='RUNNING', total_requests=2)
            result = mcp_tools.confirm_run_api_scenario(
                self.ctx, preview['confirm_token'])
        self.assertEqual(result['execution_id'], 99)
        self.assertEqual(result['total_requests'], 2)

    def test_run_api_suite_preview_counts_scenarios(self):
        TestSuiteItem.objects.create(
            test_suite=self.api_suite, scenario_case=self.scenario)
        preview = mcp_tools.preview_run_api_suite(self.ctx, suite_id=self.api_suite.id)
        self.assertIn('1 个场景用例', preview['preview'])

    def test_run_api_suite_accepts_environment_override(self):
        from apps.api_testing.models import Environment
        environment = Environment.objects.create(
            name='测试环境', scope='LOCAL', project=self.api_project, created_by=self.user)
        TestSuiteItem.objects.create(
            test_suite=self.api_suite, scenario_case=self.scenario)
        preview = mcp_tools.preview_run_api_suite(
            self.ctx, suite_id=self.api_suite.id, environment_id=environment.id)
        self.assertIn(f'执行环境 {environment.id}', preview['preview'])
        with patch('apps.api_testing.mcp_runner.start_suite_execution') as starter:
            starter.return_value = ApiSuiteExecution(
                id=55, test_suite=self.api_suite, status='RUNNING', total_scenarios=1)
            result = mcp_tools.confirm_run_api_suite(self.ctx, preview['confirm_token'])
        self.assertEqual(result['execution_id'], 55)
        self.assertEqual(starter.call_args.kwargs['environment'].id, environment.id)

    def test_run_ui_case_rejects_selenium_for_storage_state(self):
        case = UiCase.objects.create(
            name='登录态用例', project=self.ui_project, created_by=self.user)
        TestCaseStep.objects.create(
            test_case=case, step_number=1, action_type='storageState',
            action_params={'operation': 'switch'})
        preview = mcp_tools.preview_run_ui_case(
            self.ctx, case_id=case.id, engine='selenium')
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_run_ui_case(self.ctx, preview['confirm_token'])
        self.assertIn('playwright', str(ctx.exception))

    def test_run_ui_suite_requires_test_cases(self):
        preview = mcp_tools.preview_run_ui_suite(self.ctx, suite_id=self.ui_suite.id)
        with self.assertRaises(ToolError):
            mcp_tools.confirm_run_ui_suite(self.ctx, preview['confirm_token'])

    def test_run_ui_suite_returns_execution_id(self):
        case = UiCase.objects.create(
            name='登录', project=self.ui_project, created_by=self.user)
        TestSuiteTestCase.objects.create(test_suite=self.ui_suite, test_case=case)
        preview = mcp_tools.preview_run_ui_suite(self.ctx, suite_id=self.ui_suite.id)
        with patch('apps.ui_automation.mcp_runner.start_suite_execution') as starter:
            starter.return_value = UiExecution(
                id=77, project=self.ui_project, test_suite=self.ui_suite,
                status='RUNNING', engine='playwright', browser='chrome')
            result = mcp_tools.confirm_run_ui_suite(self.ctx, preview['confirm_token'])
        self.assertEqual(result['execution_id'], 77)

    def test_permission_denied_for_foreign_suite(self):
        foreign_project = _create_api_project(self.other, None, '别人的 API 项目')
        foreign_suite = ApiSuite.objects.create(
            project=foreign_project, name='外部套件', created_by=self.other)
        preview = mcp_tools.preview_run_api_suite(self.ctx, suite_id=foreign_suite.id)
        with self.assertRaises(ToolError):
            mcp_tools.confirm_run_api_suite(self.ctx, preview['confirm_token'])


class ExecutionResultToolTest(BaseFixture):
    def setUp(self):
        super().setUp()
        self.scenario = ScenarioCase.objects.create(
            project=self.api_project, name='登录场景', created_by=self.user)
        self.api_suite = ApiSuite.objects.create(
            project=self.api_project, name='冒烟', created_by=self.user)
        self.ui_suite = UiSuite.objects.create(project=self.ui_project, name='UI 冒烟')

    def test_get_api_scenario_execution(self):
        execution = ApiExecution.objects.create(
            scenario_case=self.scenario, status='COMPLETED',
            total_requests=1, passed_requests=1, failed_requests=0,
            results=[{'name': '登录', 'passed': True, 'status_code': 200}],
            executed_by=self.user)
        result = mcp_tools.get_api_scenario_execution(
            self.ctx, execution_id=execution.id)
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['results'][0]['status_code'], 200)
        self.assertNotIn('hint', result)

    def test_get_api_scenario_execution_running_hint(self):
        execution = ApiExecution.objects.create(
            scenario_case=self.scenario, status='RUNNING', executed_by=self.user)
        result = mcp_tools.get_api_scenario_execution(
            self.ctx, execution_id=execution.id)
        self.assertIn('hint', result)

    def test_get_api_suite_execution(self):
        execution = ApiSuiteExecution.objects.create(
            test_suite=self.api_suite, status='FAILED',
            total_scenarios=2, passed_scenarios=1, failed_scenarios=1,
            results=[{'scenario_case_id': self.scenario.id, 'status': 'FAILED'}],
            executed_by=self.user)
        result = mcp_tools.get_api_suite_execution(
            self.ctx, execution_id=execution.id)
        self.assertEqual(result['failed_scenarios'], 1)
        self.assertEqual(result['results'][0]['scenario_case_id'], self.scenario.id)

    def test_get_ui_suite_execution(self):
        # TestExecutor 落库的逐用例结果字段为 test_case_id / test_case_name
        execution = UiExecution.objects.create(
            project=self.ui_project, test_suite=self.ui_suite, status='SUCCESS',
            total_cases=1, passed_cases=1, duration=3.5,
            result_data={'test_cases': [
                {'test_case_id': 1, 'test_case_name': '登录', 'status': 'passed'}]},
            executed_by=self.user)
        result = mcp_tools.get_ui_suite_execution(
            self.ctx, execution_id=execution.id)
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(result['pass_rate'], 100.0)
        self.assertEqual(result['cases'][0]['test_case_id'], 1)

    def test_permission_denied(self):
        foreign_project = _create_api_project(self.other, None, '别人的 API 项目')
        foreign_scenario = ScenarioCase.objects.create(
            project=foreign_project, name='外部场景', created_by=self.other)
        execution = ApiExecution.objects.create(
            scenario_case=foreign_scenario, status='COMPLETED', executed_by=self.other)
        with self.assertRaises(ToolError):
            mcp_tools.get_api_scenario_execution(self.ctx, execution_id=execution.id)


class ApiSuiteRunnerTest(BaseFixture):
    """套件执行汇总逻辑：用替身替换真实 HTTP 执行，验证状态与汇总落库。"""

    def setUp(self):
        super().setUp()
        self.scenario_ok = ScenarioCase.objects.create(
            project=self.api_project, name='通过场景', created_by=self.user)
        self.scenario_fail = ScenarioCase.objects.create(
            project=self.api_project, name='失败场景', created_by=self.user)
        self.suite = ApiSuite.objects.create(
            project=self.api_project, name='混合套件', created_by=self.user)
        TestSuiteItem.objects.create(
            test_suite=self.suite, scenario_case=self.scenario_ok, order=0)
        TestSuiteItem.objects.create(
            test_suite=self.suite, scenario_case=self.scenario_fail, order=1)

    def _run_worker(self, results):
        from apps.api_testing import mcp_runner
        from apps.api_testing import utils as api_utils

        suite_execution = ApiSuiteExecution.objects.create(
            test_suite=self.suite, status='RUNNING', executed_by=self.user,
            total_scenarios=2)
        # 汇总逻辑已收敛到 utils.execute_test_suite，替身要打在 utils 上。
        with patch.object(api_utils, 'execute_scenario_case', side_effect=results), \
                patch.object(mcp_runner, 'connection'):
            mcp_runner._run_suite_worker(suite_execution, self.suite, None, self.user)
        suite_execution.refresh_from_db()
        return suite_execution

    def test_aggregates_passed_and_failed(self):
        execution = self._run_worker([
            {'success': True, 'passed_count': 2, 'failed_count': 0, 'total_count': 2},
            {'success': True, 'passed_count': 1, 'failed_count': 1, 'total_count': 2},
        ])
        self.assertEqual(execution.status, 'FAILED')
        self.assertEqual(execution.passed_scenarios, 1)
        self.assertEqual(execution.failed_scenarios, 1)
        self.assertEqual(len(execution.results), 2)
        self.assertIsNotNone(execution.end_time)
        self.assertEqual(execution.results[0]['scenario_case_name'], '通过场景')

    def test_all_passed_marks_completed(self):
        execution = self._run_worker([
            {'success': True, 'passed_count': 1, 'failed_count': 0, 'total_count': 1},
            {'success': True, 'passed_count': 1, 'failed_count': 0, 'total_count': 1},
        ])
        self.assertEqual(execution.status, 'COMPLETED')
        self.assertEqual(execution.passed_scenarios, 2)

    def test_start_requires_enabled_scenarios(self):
        empty_suite = ApiSuite.objects.create(
            project=self.api_project, name='空套件', created_by=self.user)
        from apps.api_testing.mcp_runner import start_suite_execution
        with self.assertRaises(ValueError):
            start_suite_execution(empty_suite, self.user)

    def test_zero_request_result_is_not_passed(self):
        """0 通过 0 失败不能被算作通过（防御性判定）。"""
        execution = self._run_worker([
            {'success': True, 'passed_count': 0, 'failed_count': 0, 'total_count': 0},
            {'success': True, 'passed_count': 1, 'failed_count': 0, 'total_count': 1},
        ])
        self.assertEqual(execution.status, 'FAILED')
        self.assertEqual(execution.passed_scenarios, 1)
        self.assertEqual(execution.failed_scenarios, 1)
        self.assertEqual(execution.results[0]['status'], 'FAILED')
        self.assertIn('未包含启用的请求', execution.results[0]['error'])

    def test_worker_reuses_prebuilt_execution(self):
        """异步路径只复用预建记录，不再二次创建套件执行记录。"""
        execution = self._run_worker([
            {'success': True, 'passed_count': 1, 'failed_count': 0, 'total_count': 1},
            {'success': True, 'passed_count': 1, 'failed_count': 0, 'total_count': 1},
        ])
        self.assertEqual(
            ApiSuiteExecution.objects.filter(test_suite=self.suite).count(), 1)
        self.assertEqual(execution.status, 'COMPLETED')
        self.assertIsNotNone(execution.end_time)

    def test_worker_marks_failed_when_runner_raises(self):
        """后台线程内任何未捕获异常都必须把预建记录落成 FAILED。"""
        from apps.api_testing import mcp_runner

        suite_execution = ApiSuiteExecution.objects.create(
            test_suite=self.suite, status='RUNNING', executed_by=self.user,
            total_scenarios=2)
        with patch.object(mcp_runner, 'execute_test_suite',
                          side_effect=RuntimeError('查询失败')), \
                patch.object(mcp_runner, 'connection'):
            mcp_runner._run_suite_worker(suite_execution, self.suite, None, self.user)
        suite_execution.refresh_from_db()

        self.assertEqual(suite_execution.status, 'FAILED')
        self.assertIn('查询失败', suite_execution.error_message)
        self.assertIsNotNone(suite_execution.end_time)

    def test_empty_scenario_execution_marks_failed(self):
        """场景本身没有启用请求时，执行记录必须落 FAILED 而不是 COMPLETED。"""
        from apps.api_testing.utils import execute_scenario_case
        empty_scenario = ScenarioCase.objects.create(
            project=self.api_project, name='空场景', created_by=self.user)

        result = execute_scenario_case(empty_scenario, None, self.user)

        self.assertFalse(result['success'])
        self.assertEqual(result['total_count'], 0)
        self.assertIn('未包含启用的请求', result['error'])
        execution = ApiExecution.objects.get(id=result['execution_id'])
        self.assertEqual(execution.status, 'FAILED')
        self.assertEqual(execution.failed_requests, 0)
        self.assertEqual(execution.results[0]['error'], '场景用例未包含启用的请求，无法执行')

    def test_suite_with_only_empty_scenario_fails(self):
        """整条套件都是空场景时，套件执行结果为 FAILED。"""
        empty_scenario = ScenarioCase.objects.create(
            project=self.api_project, name='空场景', created_by=self.user)
        suite = ApiSuite.objects.create(
            project=self.api_project, name='空场景套件', created_by=self.user)
        TestSuiteItem.objects.create(test_suite=suite, scenario_case=empty_scenario, order=0)

        from apps.api_testing import mcp_runner
        suite_execution = ApiSuiteExecution.objects.create(
            test_suite=suite, status='RUNNING', executed_by=self.user, total_scenarios=1)
        with patch.object(mcp_runner, 'connection'):
            mcp_runner._run_suite_worker(suite_execution, suite, None, self.user)
        suite_execution.refresh_from_db()

        self.assertEqual(suite_execution.status, 'FAILED')
        self.assertEqual(suite_execution.passed_scenarios, 0)
        self.assertEqual(suite_execution.failed_scenarios, 1)


class ExecuteTestSuiteRecordTest(BaseFixture):
    """页面/定时任务路径（utils.execute_test_suite）也要落套件执行记录。"""

    def setUp(self):
        super().setUp()
        self.scenario = ScenarioCase.objects.create(
            project=self.api_project, name='场景', created_by=self.user)
        self.suite = ApiSuite.objects.create(
            project=self.api_project, name='套件', created_by=self.user)
        TestSuiteItem.objects.create(test_suite=self.suite, scenario_case=self.scenario)

    def test_records_suite_execution(self):
        from apps.api_testing import utils as api_utils

        with patch.object(api_utils, 'execute_scenario_case', return_value={
                'success': True, 'execution_id': 7, 'passed_count': 1,
                'failed_count': 0, 'total_count': 1}):
            result = api_utils.execute_test_suite(self.suite, None, self.user)

        execution = ApiSuiteExecution.objects.get(id=result['suite_execution_id'])
        self.assertEqual(execution.status, 'COMPLETED')
        self.assertEqual(execution.total_scenarios, 1)
        self.assertEqual(execution.passed_scenarios, 1)
        self.assertEqual(execution.results[0]['execution_id'], 7)
        self.assertIsNotNone(execution.end_time)


class PriorityResolutionTest(TestCase):
    """优先级接受项目模型枚举及其中文别名，不再兼容 P0-P3。"""

    def test_accepts_functional_case_values(self):
        self.assertEqual(
            mcp_actions._resolve_priority(
                'HIGH', FunctionalTestCase.PRIORITY_CHOICES, 'medium'), 'high')
        self.assertEqual(
            mcp_actions._resolve_priority(
                'critical', FunctionalTestCase.PRIORITY_CHOICES, 'medium'), 'critical')

    def test_accepts_chinese_aliases(self):
        aliases = {'低': 'low', '中': 'medium', '高': 'high', '紧急': 'critical'}
        for label, expected in aliases.items():
            self.assertEqual(
                mcp_actions._resolve_priority(
                    label, FunctionalTestCase.PRIORITY_CHOICES, 'medium'), expected)

    def test_ui_priority_uses_ui_choices(self):
        self.assertEqual(
            mcp_actions._resolve_priority('low', UiCase.PRIORITY_CHOICES, 'medium'), 'low')
        self.assertEqual(
            mcp_actions._resolve_priority('高', UiCase.PRIORITY_CHOICES, 'medium'), 'high')
        # critical / 紧急 不在 UI 用例枚举内，必须拒绝
        for invalid in ('critical', '紧急'):
            with self.assertRaises(mcp_actions.McpActionError):
                mcp_actions._resolve_priority(invalid, UiCase.PRIORITY_CHOICES, 'medium')

    def test_rejects_p0_p3(self):
        for alias in ('P0', 'P1', 'P2', 'P3', '最高'):
            with self.assertRaises(mcp_actions.McpActionError):
                mcp_actions._resolve_priority(
                    alias, FunctionalTestCase.PRIORITY_CHOICES, 'medium')

    def test_empty_value_falls_back_to_default(self):
        self.assertEqual(
            mcp_actions._resolve_priority(
                None, FunctionalTestCase.PRIORITY_CHOICES, 'medium'), 'medium')

    def test_coerce_steps(self):
        steps = mcp_actions._coerce_steps([
            {'action': '打开页面', 'expected': '页面加载'},
            {'action': '点击登录'},
        ])
        self.assertEqual(steps, [
            {'action': '打开页面', 'expected': '页面加载'},
            {'action': '点击登录', 'expected': ''},
        ])
        self.assertEqual(
            mcp_actions._coerce_steps('#1.打开页面\n#2.点击登录'),
            [{'action': '1.打开页面', 'expected': ''}, {'action': '2.点击登录', 'expected': ''}],
        )

    def test_coerce_steps_keeps_identity(self):
        """id / sort_order 必须透传，供按主键增量保存使用。"""
        steps = mcp_actions._coerce_steps([
            {'id': 12, 'sort_order': 3, 'action': '打开页面', 'expected': '页面加载'},
            {'action': '点击登录'},
        ])
        self.assertEqual(steps[0], {'id': 12, 'sort_order': 3,
                                    'action': '打开页面', 'expected': '页面加载'})
        self.assertNotIn('id', steps[1])
        self.assertNotIn('sort_order', steps[1])


class PriorityAliasEndToEndTest(BaseFixture):
    """中文优先级别名走完整工具链路（preview → confirm）落库正确。"""

    def test_create_functional_case_with_chinese_priority(self):
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '中文优先级用例', 'priority': '紧急'})
        result = mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertEqual(result['priority'], 'critical')
        self.assertEqual(FunctionalTestCase.objects.get(id=result['testcase_id']).priority,
                         'critical')

    def test_create_ui_case_with_chinese_priority(self):
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '中文优先级 UI 用例', 'priority': '高'})
        result = mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])
        self.assertEqual(result['priority'], 'high')
        self.assertEqual(UiCase.objects.get(id=result['case_id']).priority, 'high')

    def test_ui_case_rejects_functional_only_priority(self):
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '非法优先级', 'priority': '紧急'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])
        self.assertIn('高/中/低', str(ctx.exception))
        self.assertFalse(UiCase.objects.filter(name='非法优先级').exists())


class CreateUiElementToolTest(BaseFixture):
    """新建元素：页面自动挂分组、定位器去重、参数校验与权限。"""

    def setUp(self):
        super().setUp()
        self.strategy = LocatorStrategy.objects.create(name='CSS')

    def _create(self, data, project_id=None):
        preview = mcp_tools.preview_create_ui_element(
            self.ctx, project_id=project_id or self.ui_project.id, data=data)
        return mcp_tools.confirm_create_ui_element(self.ctx, preview['confirm_token'])

    def test_creates_element_and_attaches_page_group(self):
        result = self._create({
            'name': '登录按钮', 'element_type': 'BUTTON', 'page': '登录页',
            'locator_strategy': 'CSS', 'locator_value': '#login-btn',
            'backup_locators': [{'strategy': 'XPath', 'value': '//button'}]})

        self.assertFalse(result['reused'])
        element = Element.objects.get(id=result['element_id'])
        self.assertEqual(element.page, '登录页')
        self.assertEqual(element.locator_strategy_id, self.strategy.id)
        # page 未配合 group_id 时自动建/挂同名页面分组
        self.assertIsNotNone(element.group_id)
        self.assertEqual(element.group.name, '登录页')
        self.assertEqual(len(element.backup_locators), 1)
        self.assertEqual(element.created_by_id, self.user.id)

    def test_reuses_element_with_same_locator(self):
        first = self._create({'name': '登录按钮', 'locator_strategy': 'CSS',
                              'locator_value': '#login-btn'})
        second = self._create({'name': '登录按钮-重复', 'locator_strategy': 'CSS',
                               'locator_value': '#login-btn'})
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['element_id'], second['element_id'])
        self.assertEqual(Element.objects.count(), 1)

    def test_list_reflects_new_element_immediately(self):
        """写操作后 list 缓存必须失效，否则「列表→创建→再列表」会读到旧数据。"""
        before = mcp_tools.list_ui_elements(self.ctx, project_id=self.ui_project.id)
        self.assertEqual(before['total'], 0)
        self._create({'name': '登录按钮', 'locator_strategy': 'CSS',
                      'locator_value': '#login-btn'})
        after = mcp_tools.list_ui_elements(self.ctx, project_id=self.ui_project.id)
        self.assertEqual(after['total'], 1)
        self.assertEqual(after['items'][0]['name'], '登录按钮')

    def test_rejects_unknown_locator_strategy(self):
        preview = mcp_tools.preview_create_ui_element(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '按钮', 'locator_strategy': 'NOT_A_STRATEGY',
                  'locator_value': '#x'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_ui_element(self.ctx, preview['confirm_token'])
        self.assertIn('不存在', str(ctx.exception))
        self.assertEqual(Element.objects.count(), 0)

    def test_rejects_invalid_element_type(self):
        preview = mcp_tools.preview_create_ui_element(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '按钮', 'element_type': 'NOPE',
                  'locator_strategy': 'CSS', 'locator_value': '#x'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_ui_element(self.ctx, preview['confirm_token'])
        self.assertIn('element_type', str(ctx.exception))

    def test_rejects_foreign_project(self):
        foreign = _create_ui_project(self.other, None, '别人的 UI 项目')
        preview = mcp_tools.preview_create_ui_element(
            self.ctx, project_id=foreign.id,
            data={'name': '按钮', 'locator_strategy': 'CSS', 'locator_value': '#x'})
        with self.assertRaises(ToolError):
            mcp_tools.confirm_create_ui_element(self.ctx, preview['confirm_token'])
        self.assertEqual(Element.objects.count(), 0)


class CreateApiRequestToolTest(BaseFixture):
    """新建接口请求：集合解析/自动新建、参数校验与重复提示。"""

    def setUp(self):
        super().setUp()
        self.collection = ApiCollection.objects.create(name='用户模块', project=self.api_project)

    def test_creates_request_in_existing_collection(self):
        preview = mcp_tools.preview_create_api_request(
            self.ctx, project_id=self.api_project.id,
            data={'name': '登录', 'method': 'post', 'url': '{{base_url}}/api/login',
                  'collection_id': self.collection.id, 'body': {'username': 'admin'}})
        self.assertIn('用户模块', preview['preview'])
        result = mcp_tools.confirm_create_api_request(self.ctx, preview['confirm_token'])

        self.assertEqual(result['collection_id'], self.collection.id)
        self.assertFalse(result['collection_created'])
        self.assertEqual(result['method'], 'POST')
        api_request = ApiRequest.objects.get(id=result['request_id'])
        self.assertEqual(api_request.body, {'username': 'admin'})
        self.assertEqual(api_request.created_by_id, self.user.id)

    def test_creates_collection_when_name_missing(self):
        preview = mcp_tools.preview_create_api_request(
            self.ctx, project_id=self.api_project.id,
            data={'name': '下单', 'method': 'POST', 'url': '/api/order',
                  'collection_name': '订单模块'})
        self.assertIn('将新建', preview['preview'])
        result = mcp_tools.confirm_create_api_request(self.ctx, preview['confirm_token'])
        self.assertTrue(result['collection_created'])
        self.assertEqual(result['collection_name'], '订单模块')
        self.assertTrue(ApiCollection.objects.filter(
            project=self.api_project, name='订单模块').exists())

    def test_reuses_collection_by_name(self):
        result = mcp_tools.confirm_create_api_request(
            self.ctx,
            mcp_tools.preview_create_api_request(
                self.ctx, project_id=self.api_project.id,
                data={'name': '用户详情', 'url': '/api/user/1',
                      'collection_name': '用户模块'})['confirm_token'])
        self.assertFalse(result['collection_created'])
        self.assertEqual(result['collection_id'], self.collection.id)

    def test_requires_collection(self):
        preview = mcp_tools.preview_create_api_request(
            self.ctx, project_id=self.api_project.id,
            data={'name': '无集合接口', 'url': '/api/x'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_api_request(self.ctx, preview['confirm_token'])
        self.assertIn('collection_id 或 collection_name', str(ctx.exception))
        self.assertEqual(ApiRequest.objects.count(), 0)

    def test_invalid_collection_leaves_no_orphan(self):
        preview = mcp_tools.preview_create_api_request(
            self.ctx, project_id=self.api_project.id,
            data={'name': '下单', 'url': '/api/order', 'collection_name': '新目录'})
        # 预览后集合还不存在，确认时才会创建
        self.assertFalse(ApiCollection.objects.filter(name='新目录').exists())
        result = mcp_tools.confirm_create_api_request(self.ctx, preview['confirm_token'])
        self.assertTrue(result['collection_created'])

    def test_rejects_invalid_method_and_reports_similar(self):
        preview = mcp_tools.preview_create_api_request(
            self.ctx, project_id=self.api_project.id,
            data={'name': '登录', 'method': 'FETCH', 'url': '/api/login',
                  'collection_id': self.collection.id})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_api_request(self.ctx, preview['confirm_token'])
        self.assertIn('method', str(ctx.exception))

        ApiRequest.objects.create(collection=self.collection, name='登录', method='POST',
                                  url='/api/login', created_by=self.user)
        result = mcp_tools.confirm_create_api_request(
            self.ctx,
            mcp_tools.preview_create_api_request(
                self.ctx, project_id=self.api_project.id,
                data={'name': '登录-重复', 'method': 'POST', 'url': '/api/login',
                      'collection_id': self.collection.id})['confirm_token'])
        self.assertEqual(len(result['similar_requests']), 1)
        self.assertEqual(result['similar_requests'][0]['name'], '登录')


class TestCaseQueryToolTest(BaseFixture):
    """功能用例读取：模块过滤、关联状态过滤、模块树与用例详情。"""

    def setUp(self):
        super().setUp()
        from apps.testcases.models import TestCaseModule, TestCaseStep

        self.login_module = TestCaseModule.objects.create(project=self.project, name='登录')
        self.child_module = TestCaseModule.objects.create(
            project=self.project, name='子模块', parent=self.login_module)
        self.linked_case = FunctionalTestCase.objects.create(
            project=self.project, title='已关联 UI 的用例',
            author=self.user, module=self.login_module)
        self.unlinked_case = FunctionalTestCase.objects.create(
            project=self.project, title='未关联的用例',
            author=self.user, module=self.child_module)
        self.loose_case = FunctionalTestCase.objects.create(
            project=self.project, title='无模块用例', author=self.user)
        TestCaseStep.objects.create(testcase=self.linked_case, sort_order=0,
                                    action='输入账号', expected='进入首页')

        self.ui_case = UiCase.objects.create(
            project=self.ui_project, name='登录-正常登录', created_by=self.user)
        TestCaseAutomationLink.objects.create(
            test_case=self.linked_case, case_type='ui_test_case',
            object_id=self.ui_case.id, linked_by=self.user)

    def test_filters_by_module_with_children(self):
        result = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, module_id=self.login_module.id)
        self.assertEqual({i['id'] for i in result['items']},
                         {self.linked_case.id, self.unlinked_case.id})

        only_parent = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, module_id=self.login_module.id,
            include_children=False)
        self.assertEqual([i['id'] for i in only_parent['items']], [self.linked_case.id])

    def test_filters_by_keyword(self):
        result = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, keyword='未关联')
        self.assertEqual([i['id'] for i in result['items']], [self.unlinked_case.id])

    def test_returns_automation_links(self):
        result = mcp_tools.list_testcases(self.ctx, project_id=self.project.id)
        item = next(i for i in result['items'] if i['id'] == self.linked_case.id)
        self.assertEqual(len(item['automation_links']), 1)
        link = item['automation_links'][0]
        self.assertEqual(link['type'], 'ui_test_case')
        self.assertEqual(link['target_name'], '登录-正常登录')
        self.assertTrue(link['target_exists'])

        plain = next(i for i in result['items'] if i['id'] == self.unlinked_case.id)
        self.assertEqual(plain['automation_links'], [])

    def test_filters_by_linked_status(self):
        def ids(**kwargs):
            return {i['id'] for i in mcp_tools.list_testcases(
                self.ctx, project_id=self.project.id, **kwargs)['items']}

        self.assertEqual(ids(linked='ui'), {self.linked_case.id})
        self.assertEqual(ids(linked='api'), set())
        self.assertEqual(ids(linked='ui_missing'),
                         {self.unlinked_case.id, self.loose_case.id})
        self.assertEqual(ids(linked='api_missing'),
                         {self.linked_case.id, self.unlinked_case.id, self.loose_case.id})
        self.assertEqual(ids(linked='any'), {self.linked_case.id})
        self.assertEqual(ids(linked='none'),
                         {self.unlinked_case.id, self.loose_case.id})
        self.assertEqual(ids(linked='all_missing'),
                         {self.unlinked_case.id, self.loose_case.id})

    def test_invalid_linked_value_rejected(self):
        with self.assertRaises(ToolError):
            mcp_tools.list_testcases(self.ctx, project_id=self.project.id, linked='oops')

    def test_list_testcase_modules_returns_path_and_counts(self):
        result = mcp_tools.list_testcase_modules(self.ctx, project_id=self.project.id)
        modules = {m['name']: m for m in result['items']}
        self.assertEqual(modules['登录']['path'], '登录')
        self.assertEqual(modules['登录']['level'], 0)
        self.assertEqual(modules['登录']['case_count'], 1)
        self.assertEqual(modules['子模块']['path'], '登录/子模块')
        self.assertEqual(modules['子模块']['level'], 1)
        self.assertEqual(modules['子模块']['parent_name'], '登录')
        self.assertEqual(result['unassigned_case_count'], 1)

    def test_get_testcase_returns_steps_and_links(self):
        result = mcp_tools.get_testcase(self.ctx, testcase_id=self.linked_case.id)
        self.assertEqual(result['module_path'], '登录')
        self.assertEqual(result['steps'][0]['action'], '输入账号')
        self.assertTrue(result['has_ui_automation'])
        self.assertFalse(result['has_api_automation'])

    def test_get_testcase_returns_step_ids(self):
        """步骤 id 必须回传，否则调用方无法按主键增量更新。"""
        result = mcp_tools.get_testcase(self.ctx, testcase_id=self.linked_case.id)
        step = result['steps'][0]
        self.assertIn('id', step)
        self.assertTrue(
            self.linked_case.step_details.filter(id=step['id']).exists())

    def test_get_testcase_permission_denied(self):
        foreign_project = Project.objects.create(name='别人项目', owner=self.other)
        foreign_case = FunctionalTestCase.objects.create(
            project=foreign_project, title='别人用例',
            author=self.other)
        with self.assertRaises(ToolError):
            mcp_tools.get_testcase(self.ctx, testcase_id=foreign_case.id)


class ApiRequestQueryToolTest(BaseFixture):
    """接口请求与集合读取：关键词/方法/集合过滤与目录路径。"""

    def setUp(self):
        super().setUp()
        self.collection = ApiCollection.objects.create(name='用户模块', project=self.api_project)
        self.child_collection = ApiCollection.objects.create(
            name='子目录', project=self.api_project, parent=self.collection)
        self.login_request = ApiRequest.objects.create(
            collection=self.collection, name='登录', method='POST',
            url='/api/login', created_by=self.user)
        self.profile_request = ApiRequest.objects.create(
            collection=self.child_collection, name='用户详情', method='GET',
            url='/api/user/1', created_by=self.user)

    def test_filters_by_keyword_method_and_collection(self):
        self.assertEqual(
            [i['id'] for i in mcp_tools.list_api_requests(
                self.ctx, project_id=self.api_project.id, keyword='login')['items']],
            [self.login_request.id])
        self.assertEqual(
            [i['id'] for i in mcp_tools.list_api_requests(
                self.ctx, project_id=self.api_project.id, method='get')['items']],
            [self.profile_request.id])
        self.assertEqual(
            [i['id'] for i in mcp_tools.list_api_requests(
                self.ctx, project_id=self.api_project.id,
                collection_id=self.child_collection.id)['items']],
            [self.profile_request.id])

    def test_returns_collection_fields(self):
        result = mcp_tools.list_api_requests(self.ctx, project_id=self.api_project.id)
        item = next(i for i in result['items'] if i['id'] == self.profile_request.id)
        self.assertEqual(item['collection_id'], self.child_collection.id)
        self.assertEqual(item['collection_name'], '子目录')

    def test_invalid_method_rejected(self):
        with self.assertRaises(ToolError):
            mcp_tools.list_api_requests(
                self.ctx, project_id=self.api_project.id, method='FETCH')

    def test_list_api_collections_with_path_and_count(self):
        result = mcp_tools.list_api_collections(self.ctx, project_id=self.api_project.id)
        collections = {c['name']: c for c in result['items']}
        self.assertEqual(result['total'], 2)
        self.assertEqual(collections['用户模块']['request_count'], 1)
        self.assertEqual(collections['子目录']['path'], '用户模块/子目录')
        self.assertEqual(collections['子目录']['level'], 1)
        self.assertEqual(collections['子目录']['parent_name'], '用户模块')


class ProjectBindingToolTest(BaseFixture):
    """项目列表带回 UI/API 自动化项目 ID，供后续工具直接使用。"""

    def test_returns_automation_project_ids(self):
        result = mcp_tools.list_projects(self.ctx)
        item = next(i for i in result['items'] if i['id'] == self.project.id)
        self.assertEqual(item['ui_project_id'], self.ui_project.id)
        self.assertEqual(item['api_project_id'], self.api_project.id)

    def test_null_when_automation_project_missing(self):
        bare = Project.objects.create(name='只有用例的项目', owner=self.user)
        result = mcp_tools.list_projects(self.ctx)
        item = next(i for i in result['items'] if i['id'] == bare.id)
        self.assertIsNone(item['ui_project_id'])
        self.assertIsNone(item['api_project_id'])


class UiSuiteExecutionLinkTest(BaseFixture):
    """逐用例执行记录按父套件执行 ID 关联：并发批次不串批，历史数据退回时间窗。"""

    def setUp(self):
        super().setUp()
        self.suite = UiSuite.objects.create(
            project=self.ui_project, name='登录套件')
        self.case_a = UiCase.objects.create(
            project=self.ui_project, name='用例A', created_by=self.user)
        self.case_b = UiCase.objects.create(
            project=self.ui_project, name='用例B', created_by=self.user)
        TestSuiteTestCase.objects.create(
            test_suite=self.suite, test_case=self.case_a, order=0)
        TestSuiteTestCase.objects.create(
            test_suite=self.suite, test_case=self.case_b, order=1)

        now = timezone.now()
        # 两批执行时间窗完全重叠：只按时间窗过滤必然串批
        self.batch_a = UiExecution.objects.create(
            project=self.ui_project, test_suite=self.suite, status='SUCCESS',
            started_at=now, finished_at=now, executed_by=self.user)
        self.batch_b = UiExecution.objects.create(
            project=self.ui_project, test_suite=self.suite, status='SUCCESS',
            started_at=now, finished_at=now, executed_by=self.user)

        self.child_a = TestCaseExecution.objects.create(
            test_case=self.case_a, project=self.ui_project, test_suite=self.suite,
            suite_execution=self.batch_a, execution_source='suite', status='passed',
            created_by=self.user)
        self.child_b = TestCaseExecution.objects.create(
            test_case=self.case_b, project=self.ui_project, test_suite=self.suite,
            suite_execution=self.batch_b, execution_source='suite', status='failed',
            created_by=self.user)

    def test_filters_case_executions_by_parent_execution(self):
        result_a = mcp_tools.get_ui_suite_execution(self.ctx, execution_id=self.batch_a.id)
        self.assertEqual([c['id'] for c in result_a['case_executions']], [self.child_a.id])

        result_b = mcp_tools.get_ui_suite_execution(self.ctx, execution_id=self.batch_b.id)
        self.assertEqual([c['id'] for c in result_b['case_executions']], [self.child_b.id])
        self.assertEqual(result_b['case_executions'][0]['status'], 'failed')

    def test_legacy_record_falls_back_to_time_window(self):
        """没有 suite_execution 外键的历史记录仍能按时间窗查到。"""
        legacy = TestCaseExecution.objects.create(
            test_case=self.case_b, project=self.ui_project, test_suite=self.suite,
            execution_source='suite', status='passed', created_by=self.user)
        empty_parent = UiExecution.objects.create(
            project=self.ui_project, test_suite=self.suite, status='SUCCESS',
            started_at=legacy.created_at, finished_at=timezone.now(),
            executed_by=self.user)

        result = mcp_tools.get_ui_suite_execution(self.ctx, execution_id=empty_parent.id)
        ids = [c['id'] for c in result['case_executions']]
        self.assertIn(legacy.id, ids)

    def test_executor_links_parent_execution_only_for_suite(self):
        from apps.ui_automation.test_executor import TestExecutor

        suite_executor = TestExecutor(test_suite=self.suite, execution=self.batch_a)
        self.assertEqual(suite_executor.suite_execution_ref, self.batch_a)

        # 单用例执行的 execution 是 TestCaseExecution，不应被当成父套件执行
        case_executor = TestExecutor(test_suite=self.suite, execution=self.child_a)
        self.assertIsNone(case_executor.suite_execution_ref)


class GetApiRequestToolTest(BaseFixture):
    """接口请求详情：判断能否复用到场景用例。"""

    def setUp(self):
        super().setUp()
        self.parent = ApiCollection.objects.create(name='订单', project=self.api_project)
        self.collection = ApiCollection.objects.create(
            name='子目录', project=self.api_project, parent=self.parent)
        self.request = ApiRequest.objects.create(
            collection=self.collection, name='创建订单', method='POST',
            url='{{base_url}}/api/orders', created_by=self.user,
            headers={'Content-Type': 'application/json'},
            body={'sku': 'A1'},
            auth={'type': 'bearer', 'token': '{{token}}'},
            assertions=[{'type': 'status_code', 'expected': 200}],
            extractors=[{'name': 'order_id', 'json_path': '$.id'}],
            pre_request_script='pm.environment.set("t", 1)')
        self.scenario = ScenarioCase.objects.create(
            project=self.api_project, name='下单主流程', created_by=self.user)
        ScenarioCaseRequest.objects.create(
            scenario_case=self.scenario, request=self.request, order=0)

    def test_returns_full_configuration(self):
        result = mcp_tools.get_api_request(self.ctx, request_id=self.request.id)
        self.assertEqual(result['method'], 'POST')
        self.assertEqual(result['url'], '{{base_url}}/api/orders')
        self.assertEqual(result['collection_name'], '子目录')
        self.assertEqual(result['collection_path'], '订单/子目录')
        self.assertEqual(result['project_id'], self.api_project.id)
        self.assertEqual(result['headers'], {'Content-Type': 'application/json'})
        self.assertEqual(result['body'], {'sku': 'A1'})
        self.assertEqual(result['auth']['type'], 'bearer')
        self.assertEqual(result['assertions'][0]['type'], 'status_code')
        self.assertEqual(result['extractors'][0]['name'], 'order_id')
        self.assertTrue(result['pre_request_script'])
        self.assertTrue(result['has_assertions'])
        self.assertTrue(result['has_extractors'])

    def test_marks_write_request(self):
        self.assertTrue(
            mcp_tools.get_api_request(self.ctx, request_id=self.request.id)['is_write'])
        get_request = ApiRequest.objects.create(
            collection=self.parent, name='查询订单', method='GET',
            url='/api/orders', created_by=self.user)
        self.assertFalse(
            mcp_tools.get_api_request(self.ctx, request_id=get_request.id)['is_write'])

    def test_counts_referencing_scenarios(self):
        result = mcp_tools.get_api_request(self.ctx, request_id=self.request.id)
        self.assertEqual(result['referenced_by_scenarios'], 1)

    def test_missing_request_rejected(self):
        with self.assertRaises(ToolError):
            mcp_tools.get_api_request(self.ctx, request_id=999999)

    def test_permission_denied_for_foreign_project(self):
        foreign_project = _create_api_project(self.other, None, '别人的 API 项目')
        foreign_collection = ApiCollection.objects.create(
            name='外部', project=foreign_project)
        foreign_request = ApiRequest.objects.create(
            collection=foreign_collection, name='外部接口', method='GET',
            url='https://x.com', created_by=self.other)
        with self.assertRaises(ToolError):
            mcp_tools.get_api_request(self.ctx, request_id=foreign_request.id)


class AutomationLinkStaleToolTest(BaseFixture):
    """悬空关联：目标被删/移走时按「未关联」处理，并可用 ui_stale/api_stale 筛出。"""

    def setUp(self):
        super().setUp()
        self.case = FunctionalTestCase.objects.create(
            project=self.project, title='登录功能', author=self.user)
        self.ui_case = UiCase.objects.create(
            project=self.ui_project, name='登录-正常登录', created_by=self.user)
        TestCaseAutomationLink.objects.create(
            test_case=self.case, case_type='ui_test_case',
            object_id=self.ui_case.id, linked_by=self.user)

    def test_stale_when_target_id_missing(self):
        """object_id 指向不存在的目标（如历史脏数据）算悬空，不能算已关联。"""
        TestCaseAutomationLink.objects.filter(test_case=self.case).update(object_id=999999)
        result = mcp_tools.list_testcases(self.ctx, project_id=self.project.id)
        item = next(i for i in result['items'] if i['id'] == self.case.id)
        link = item['automation_links'][0]
        self.assertFalse(link['target_exists'])

        stale = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, linked='ui_stale')
        self.assertEqual([i['id'] for i in stale['items']], [self.case.id])

        missing = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, linked='ui_missing')
        self.assertEqual([i['id'] for i in missing['items']], [self.case.id])

        # 悬空关联也不能算作「已有自动化」
        detail = mcp_tools.get_testcase(self.ctx, testcase_id=self.case.id)
        self.assertFalse(detail['has_ui_automation'])
        self.assertFalse(detail['automation_links'][0]['target_exists'])

    def test_stale_after_target_moved_to_other_project(self):
        """目标被移到别的统一项目下后，原关联失效。"""
        other_project = Project.objects.create(name='另一个统一项目', owner=self.user)
        other_ui_project = _create_ui_project(self.user, other_project, '别的 UI 项目')
        self.ui_case.project = other_ui_project
        self.ui_case.save(update_fields=['project'])
        stale = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, linked='ui_stale')
        self.assertEqual([i['id'] for i in stale['items']], [self.case.id])

    def test_stale_after_ui_project_unbound(self):
        """自动化项目解绑统一项目后，关联不再有效。"""
        self.ui_project.workspace_project = None
        self.ui_project.save(update_fields=['workspace_project'])
        stale = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, linked='ui_stale')
        self.assertEqual([i['id'] for i in stale['items']], [self.case.id])

    def test_valid_link_not_stale(self):
        stale = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, linked='ui_stale')
        self.assertEqual(stale['total'], 0)
        linked = mcp_tools.list_testcases(
            self.ctx, project_id=self.project.id, linked='ui')
        self.assertEqual([i['id'] for i in linked['items']], [self.case.id])

    def test_delete_target_cleans_link(self):
        """目标真被删除时由信号清掉关联，不会残留悬空记录。"""
        self.ui_case.delete()
        self.assertFalse(TestCaseAutomationLink.objects.filter(
            test_case=self.case).exists())


class AutomationFolderToolTest(BaseFixture):
    """UI 用例目录 / 场景用例目录：按目录过滤、子目录递归、目录树与用例数。"""

    def setUp(self):
        super().setUp()
        from apps.api_testing.models import ScenarioCaseFolder
        from apps.ui_automation.models import TestCaseFolder

        self.ui_root = TestCaseFolder.objects.create(project=self.ui_project, name='登录')
        self.ui_child = TestCaseFolder.objects.create(
            project=self.ui_project, name='短信登录', parent=self.ui_root)
        self.ui_other = TestCaseFolder.objects.create(project=self.ui_project, name='下单')

        self.ui_case_root = UiCase.objects.create(
            project=self.ui_project, name='登录-正常登录', folder=self.ui_root,
            created_by=self.user)
        self.ui_case_child = UiCase.objects.create(
            project=self.ui_project, name='登录-短信登录', folder=self.ui_child,
            created_by=self.user)
        self.ui_case_other = UiCase.objects.create(
            project=self.ui_project, name='下单-正常下单', folder=self.ui_other,
            created_by=self.user)
        self.ui_case_loose = UiCase.objects.create(
            project=self.ui_project, name='未归目录用例', created_by=self.user)

        self.api_root = ScenarioCaseFolder.objects.create(
            project=self.api_project, name='下单')
        self.api_child = ScenarioCaseFolder.objects.create(
            project=self.api_project, name='正向流程', parent=self.api_root)
        self.api_other = ScenarioCaseFolder.objects.create(
            project=self.api_project, name='用户')

        self.api_case_root = ScenarioCase.objects.create(
            project=self.api_project, name='下单主流程', folder=self.api_root,
            created_by=self.user)
        self.api_case_child = ScenarioCase.objects.create(
            project=self.api_project, name='下单-支付成功', folder=self.api_child,
            created_by=self.user)
        self.api_case_other = ScenarioCase.objects.create(
            project=self.api_project, name='用户登录', folder=self.api_other,
            created_by=self.user)
        self.api_case_loose = ScenarioCase.objects.create(
            project=self.api_project, name='未归目录场景', created_by=self.user)

    # ---------------------------- UI 用例 ---------------------------- #
    def test_ui_cases_filter_by_folder_with_children(self):
        result = mcp_tools.list_ui_cases(
            self.ctx, project_id=self.ui_project.id, folder_id=self.ui_root.id)
        self.assertEqual({i['id'] for i in result['items']},
                         {self.ui_case_root.id, self.ui_case_child.id})

    def test_ui_cases_filter_without_children(self):
        result = mcp_tools.list_ui_cases(
            self.ctx, project_id=self.ui_project.id, folder_id=self.ui_root.id,
            include_children=False)
        self.assertEqual([i['id'] for i in result['items']], [self.ui_case_root.id])

    def test_ui_cases_filter_by_keyword(self):
        result = mcp_tools.list_ui_cases(
            self.ctx, project_id=self.ui_project.id, keyword='短信')
        self.assertEqual([i['id'] for i in result['items']], [self.ui_case_child.id])

    def test_ui_cases_return_folder_name_and_path(self):
        result = mcp_tools.list_ui_cases(self.ctx, project_id=self.ui_project.id)
        self.assertEqual(result['total'], 4)
        item = next(i for i in result['items'] if i['id'] == self.ui_case_child.id)
        self.assertEqual(item['folder_id'], self.ui_child.id)
        self.assertEqual(item['folder_name'], '短信登录')
        self.assertEqual(item['folder_path'], '登录/短信登录')
        loose = next(i for i in result['items'] if i['id'] == self.ui_case_loose.id)
        self.assertIsNone(loose['folder_id'])
        self.assertEqual(loose['folder_name'], '')
        self.assertEqual(loose['folder_path'], '')

    def test_ui_case_folder_tree(self):
        result = mcp_tools.list_ui_case_folders(self.ctx, project_id=self.ui_project.id)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['unassigned_case_count'], 1)
        folders = {f['name']: f for f in result['items']}
        self.assertEqual(folders['登录']['path'], '登录')
        self.assertEqual(folders['登录']['level'], 0)
        self.assertEqual(folders['登录']['case_count'], 1)
        self.assertEqual(folders['短信登录']['path'], '登录/短信登录')
        self.assertEqual(folders['短信登录']['level'], 1)
        self.assertEqual(folders['短信登录']['parent_name'], '登录')
        self.assertEqual(folders['短信登录']['case_count'], 1)
        self.assertEqual(folders['下单']['case_count'], 1)
        # 目录 id 可直接回传给 list_ui_cases
        child_only = mcp_tools.list_ui_cases(
            self.ctx, project_id=self.ui_project.id,
            folder_id=folders['短信登录']['id'])
        self.assertEqual([i['id'] for i in child_only['items']], [self.ui_case_child.id])

    # -------------------------- 场景用例 --------------------------- #
    def test_scenarios_filter_by_folder_with_children(self):
        result = mcp_tools.list_api_scenarios(
            self.ctx, project_id=self.api_project.id, folder_id=self.api_root.id)
        self.assertEqual({i['id'] for i in result['items']},
                         {self.api_case_root.id, self.api_case_child.id})

    def test_scenarios_filter_without_children(self):
        result = mcp_tools.list_api_scenarios(
            self.ctx, project_id=self.api_project.id, folder_id=self.api_root.id,
            include_children=False)
        self.assertEqual([i['id'] for i in result['items']], [self.api_case_root.id])

    def test_scenarios_filter_by_keyword(self):
        result = mcp_tools.list_api_scenarios(
            self.ctx, project_id=self.api_project.id, keyword='支付')
        self.assertEqual([i['id'] for i in result['items']], [self.api_case_child.id])

    def test_scenarios_return_folder_name_and_path(self):
        result = mcp_tools.list_api_scenarios(self.ctx, project_id=self.api_project.id)
        self.assertEqual(result['total'], 4)
        item = next(i for i in result['items'] if i['id'] == self.api_case_child.id)
        self.assertEqual(item['folder_name'], '正向流程')
        self.assertEqual(item['folder_path'], '下单/正向流程')
        self.assertEqual(item['request_count'], 0)

    def test_scenario_folder_tree(self):
        result = mcp_tools.list_scenario_case_folders(
            self.ctx, project_id=self.api_project.id)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['unassigned_case_count'], 1)
        folders = {f['name']: f for f in result['items']}
        self.assertEqual(folders['下单']['path'], '下单')
        self.assertEqual(folders['下单']['case_count'], 1)
        self.assertEqual(folders['正向流程']['path'], '下单/正向流程')
        self.assertEqual(folders['正向流程']['level'], 1)
        self.assertEqual(folders['正向流程']['parent_name'], '下单')
        self.assertEqual(folders['正向流程']['case_count'], 1)
        self.assertEqual(folders['用户']['case_count'], 1)

    # ---------------------------- 权限 ----------------------------- #
    def test_permission_denied_for_foreign_projects(self):
        foreign_ui = _create_ui_project(self.other, None, '别人的 UI 项目')
        foreign_api = _create_api_project(self.other, None, '别人的 API 项目')
        with self.assertRaises(ToolError):
            mcp_tools.list_ui_cases(self.ctx, project_id=foreign_ui.id)
        with self.assertRaises(ToolError):
            mcp_tools.list_ui_case_folders(self.ctx, project_id=foreign_ui.id)
        with self.assertRaises(ToolError):
            mcp_tools.list_api_scenarios(self.ctx, project_id=foreign_api.id)
        with self.assertRaises(ToolError):
            mcp_tools.list_scenario_case_folders(self.ctx, project_id=foreign_api.id)


class FolderPathCreationToolTest(BaseFixture):
    """只知目录名称（不知 ID）时按路径创建：5 个场景 + 缺失/歧义/自动建校验。"""

    def setUp(self):
        super().setUp()
        from apps.api_testing.models import ScenarioCaseFolder
        from apps.testcases.models import TestCaseModule
        from apps.ui_automation.models import TestCaseFolder

        # 功能用例模块：登录/短信登录，另在「下单」下放同名模块用于歧义
        self.module_root = TestCaseModule.objects.create(project=self.project, name='登录')
        self.module_child = TestCaseModule.objects.create(
            project=self.project, name='短信登录', parent=self.module_root)
        self.module_other = TestCaseModule.objects.create(project=self.project, name='下单')
        self.module_dup = TestCaseModule.objects.create(
            project=self.project, name='短信登录', parent=self.module_other)

        self.ui_folder_root = TestCaseFolder.objects.create(
            project=self.ui_project, name='登录')
        self.ui_folder_child = TestCaseFolder.objects.create(
            project=self.ui_project, name='短信登录', parent=self.ui_folder_root)

        self.sc_folder_root = ScenarioCaseFolder.objects.create(
            project=self.api_project, name='下单')
        self.sc_folder_child = ScenarioCaseFolder.objects.create(
            project=self.api_project, name='正向流程', parent=self.sc_folder_root)

        self.group_root = ElementGroup.objects.create(
            project=self.ui_project, name='登录页')
        self.group_child = ElementGroup.objects.create(
            project=self.ui_project, name='表单区', parent_group=self.group_root)

        self.collection_root = ApiCollection.objects.create(
            project=self.api_project, name='用户模块')
        self.collection_child = ApiCollection.objects.create(
            project=self.api_project, name='登录', parent=self.collection_root)

        self.strategy = LocatorStrategy.objects.create(name='CSS')

    def test_create_testcase_by_module_path(self):
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '短信登录成功', 'module_path': '登录/短信登录'})
        result = mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertEqual(result['module_id'], self.module_child.id)

    def test_create_testcase_by_unique_module_name(self):
        """只给名称、不给父级：全项目唯一命中时直接使用。"""
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '下单用例', 'module_path': '下单'})
        result = mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertEqual(result['module_id'], self.module_other.id)

    def test_create_ui_case_by_folder_path(self):
        preview = mcp_tools.preview_create_ui_case(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '短信登录', 'folder_path': '登录/短信登录'})
        result = mcp_tools.confirm_create_ui_case(self.ctx, preview['confirm_token'])
        self.assertEqual(UiCase.objects.get(id=result['case_id']).folder_id,
                         self.ui_folder_child.id)

    def test_create_scenario_by_folder_path(self):
        collection = ApiCollection.objects.create(project=self.api_project, name='临时')
        request = ApiRequest.objects.create(
            collection=collection, name='接口', method='GET', url='/x',
            created_by=self.user)
        preview = mcp_tools.preview_create_api_scenario_case(
            self.ctx, project_id=self.api_project.id,
            data={'name': '下单正向', 'folder_path': '下单/正向流程',
                  'requests': [{'request_id': request.id}]})
        result = mcp_tools.confirm_create_api_scenario_case(
            self.ctx, preview['confirm_token'])
        self.assertEqual(
            ScenarioCase.objects.get(id=result['scenario_case_id']).folder_id,
            self.sc_folder_child.id)

    def test_create_element_by_group_path(self):
        preview = mcp_tools.preview_create_ui_element(
            self.ctx, project_id=self.ui_project.id,
            data={'name': '用户名', 'group_path': '登录页/表单区',
                  'locator_strategy': 'CSS', 'locator_value': '#username'})
        result = mcp_tools.confirm_create_ui_element(self.ctx, preview['confirm_token'])
        self.assertEqual(Element.objects.get(id=result['element_id']).group_id,
                         self.group_child.id)

    def test_create_api_request_by_collection_path(self):
        preview = mcp_tools.preview_create_api_request(
            self.ctx, project_id=self.api_project.id,
            data={'name': '登录接口', 'method': 'POST', 'url': '/api/login',
                  'collection_path': '用户模块/登录'})
        result = mcp_tools.confirm_create_api_request(self.ctx, preview['confirm_token'])
        self.assertEqual(ApiRequest.objects.get(id=result['request_id']).collection_id,
                         self.collection_child.id)

    def test_missing_path_rejected_with_hint(self):
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '不存在模块', 'module_path': '登录/微信登录'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        message = str(ctx.exception)
        self.assertIn('用例模块路径 登录/微信登录 不存在', message)
        self.assertIn('create_missing=true', message)
        self.assertFalse(FunctionalTestCase.objects.filter(title='不存在模块').exists())

    def test_ambiguous_name_rejected_with_full_paths(self):
        """只给名称且项目内多处同名时报歧义，并列出完整路径候选。"""
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '歧义模块', 'module_path': '短信登录'})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        message = str(ctx.exception)
        self.assertIn('同名项', message)
        self.assertIn('登录/短信登录', message)
        self.assertIn('下单/短信登录', message)

    def test_create_missing_creates_last_segment(self):
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '微信登录', 'module_path': '登录/微信登录',
                  'create_missing': True})
        result = mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        from apps.testcases.models import TestCaseModule
        module = TestCaseModule.objects.get(id=result['module_id'])
        self.assertEqual(module.name, '微信登录')
        self.assertEqual(module.parent_id, self.module_root.id)

    def test_missing_intermediate_segment_rejected_even_with_create_missing(self):
        """中间层不允许自动创建，避免建出错误层级。"""
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': '层级错误', 'module_path': '不存在/子模块',
                  'create_missing': True})
        with self.assertRaises(ToolError) as ctx:
            mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertIn('请先创建上级目录', str(ctx.exception))

    def test_id_takes_precedence_over_path(self):
        preview = mcp_tools.preview_create_testcase(
            self.ctx, project_id=self.project.id,
            data={'title': 'ID 优先', 'module_id': self.module_child.id,
                  'module_path': '下单'})
        result = mcp_tools.confirm_create_testcase(self.ctx, preview['confirm_token'])
        self.assertEqual(result['module_id'], self.module_child.id)

    def test_update_testcase_by_module_path(self):
        case = FunctionalTestCase.objects.create(
            project=self.project, title='待归档', author=self.user)
        preview = mcp_tools.preview_update_testcase(
            self.ctx, testcase_id=case.id, data={'module_path': '登录/短信登录'})
        mcp_tools.confirm_update_testcase(self.ctx, preview['confirm_token'])
        case.refresh_from_db()
        self.assertEqual(case.module_id, self.module_child.id)
