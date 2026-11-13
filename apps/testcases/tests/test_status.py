from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from apps.mcp.actions import McpActionError, create_testcase_action, update_testcase_action
from apps.projects.models import Project
from apps.requirement_analysis.models import TestCaseGenerationTask as GenerationTask
from apps.requirement_analysis.views import TestCaseGenerationTaskViewSet
from apps.testcases.models import TestCase as Case
from apps.users.models import User


class TestCaseStatusEntryTests(APITestCase):
    """各创建入口默认激活，外部输入不能再创建或更新为草稿。"""

    def setUp(self):
        self.user = User.objects.create_user(username='case-status')
        self.project = Project.objects.create(name='用例状态项目', owner=self.user)
        self.client.force_authenticate(self.user)
        self.case_data = {
            'title': '登录功能', 'project_id': self.project.pk,
            'steps': [{'action': '点击登录', 'expected': '登录成功'}],
        }

    def test_rest_create_and_update_only_accept_active_or_deprecated(self):
        response = self.client.post('/api/testcases/', {
            **self.case_data, 'status': 'draft',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.data)
        self.assertFalse(Case.objects.exists())

        response = self.client.post('/api/testcases/', {
            **self.case_data, 'status': 'deprecated',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        case = Case.objects.get(pk=response.data['id'])
        self.assertEqual(case.status, 'deprecated')
        url = f'/api/testcases/{case.pk}/'
        response = self.client.patch(url, {'status': 'draft'}, format='json')
        self.assertEqual(response.status_code, 400)
        # 未提交状态时保留废弃状态，显式激活时才改变。
        response = self.client.patch(url, {'title': '修改标题'}, format='json')
        self.assertEqual(response.status_code, 200)
        case.refresh_from_db()
        self.assertEqual(case.status, 'deprecated')
        response = self.client.patch(url, {'status': 'active'}, format='json')
        self.assertEqual(response.status_code, 200)
        case.refresh_from_db()
        self.assertEqual(case.status, 'active')

    def test_mcp_create_and_update_reject_draft(self):
        arguments = {'project_id': self.project.pk, 'data': {**self.case_data, 'status': 'draft'}}
        with self.assertRaises(McpActionError):
            create_testcase_action(arguments, self.user)
        self.assertFalse(Case.objects.exists())
        arguments['data']['status'] = 'deprecated'
        result = create_testcase_action(arguments, self.user)
        self.assertEqual(result['status'], 'deprecated')
        with self.assertRaises(McpActionError):
            update_testcase_action({
                'testcase_id': result['testcase_id'], 'data': {'status': 'draft'},
            }, self.user)
        self.assertEqual(Case.objects.get().status, 'deprecated')

    def create_generation_task(self, task_id):
        return GenerationTask.objects.create(
            task_id=task_id, title='用例生成', requirement_text='登录需求',
            status='completed', final_test_cases='生成结果',
            project=self.project, created_by=self.user,
        )

    def test_ai_save_and_adopt_all_default_to_active(self):
        parsed_cases = [{
            'scenario': '生成的登录用例', 'steps': '点击登录', 'expected': '登录成功',
        }]
        for action in ('save_to_records', 'batch_adopt'):
            with self.subTest(action=action):
                task = self.create_generation_task(action)
                # 仅替换内容解析，不调用 AI；通过真实路由验证采纳落库。
                with patch.object(TestCaseGenerationTaskViewSet, '_parse_test_cases_content',
                                  return_value=parsed_cases):
                    response = self.client.post(
                        f'/api/requirement-analysis/testcase-generation/{task.task_id}/{action}/',
                        {}, format='json',
                    )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(Case.objects.filter(status='active').count(), 1)
                Case.objects.all().delete()

    def test_ai_adopt_selected_defaults_to_active_and_rejects_draft(self):
        task = self.create_generation_task('selected-status')
        url = f'/api/requirement-analysis/testcase-generation/{task.task_id}/batch-adopt-selected/'
        response = self.client.post(url, {'test_cases': [self.case_data]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Case.objects.get().status, 'active')
        Case.objects.all().delete()

        response = self.client.post(url, {
            'test_cases': [{**self.case_data, 'status': 'draft'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Case.objects.exists())
        response = self.client.post(url, {
            'test_cases': [{**self.case_data, 'status': 'deprecated'}],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Case.objects.get().status, 'deprecated')


class TestCaseStatusMigrationTests(TransactionTestCase):
    """从历史状态迁移，验证仅转换草稿且保留外键所需的项目索引。"""

    def test_migrate_draft_without_changing_deprecated_or_updated_at(self):
        previous = [('testcases', '0003_structured_testcase_steps')]
        target = [('testcases', '0004_active_status')]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        try:
            old_apps = executor.loader.project_state(previous).apps
            OldUser = old_apps.get_model('users', 'User')
            OldProject = old_apps.get_model('projects', 'Project')
            OldCase = old_apps.get_model('testcases', 'TestCase')
            user = OldUser.objects.create(username='migrate-case-status')
            project = OldProject.objects.create(name='历史项目', owner=user)
            cases = [OldCase.objects.create(
                title=value, project=project, author=user, status=value,
            ) for value in ('draft', 'active', 'deprecated')]
            old_updated_at = cases[0].updated_at
            MigrationExecutor(connection).migrate(target)

            self.assertEqual(list(Case.objects.filter(pk__in=[case.pk for case in cases])
                                  .order_by('pk').values_list('status', flat=True)),
                             ['active', 'active', 'deprecated'])
            self.assertEqual(Case.objects.get(pk=cases[0].pk).updated_at, old_updated_at)
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(cursor, 'testcases')
            self.assertTrue(any(info.get('index') and info['columns'] == ['project_id']
                                for info in constraints.values()))
            self.assertTrue(any(info.get('check') for info in constraints.values()))
        finally:
            # 即使断言失败也恢复最新结构，避免影响其他测试。
            MigrationExecutor(connection).migrate(target)
