from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ui_automation.models import (
    TestCase as UiTestCase, TestCaseExecution, TestSuite, UiProject,
)


class TestCaseExecutionListApiTests(TestCase):
    """执行摘要不读取大字段；详情、过滤和项目权限保持可用。"""

    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username='case-execution-owner')
        cls.member = get_user_model().objects.create_user(username='case-execution-member')
        cls.project = UiProject.objects.create(name='UI project', owner=cls.owner)
        cls.project.members.add(cls.member)
        cls.case = UiTestCase.objects.create(
            project=cls.project, name='Checkout case', created_by=cls.owner,
            description='case description\n' * 1000,
        )
        cls.suite = TestSuite.objects.create(project=cls.project, name='Checkout suite')
        cls.execution = TestCaseExecution.objects.create(
            project=cls.project, test_case=cls.case, test_suite=cls.suite,
            created_by=cls.owner, execution_source='suite', status='failed',
            engine='playwright', browser='firefox', headless=True, execution_time=2.5,
            execution_logs='execution log\n' * 1000,
            error_message='checkout failure\n' * 1000,
            screenshots=[{'url': 'data:image/png;base64,' + 'a' * 10000}],
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = '/api/ui-automation/test-case-executions/'
        self.params = {'project': self.project.pk, 'page_size': 1000}

    def assert_lightweight_queries(self, queries):
        for query in queries:
            if 'FROM "ui_test_case_executions"' not in query['sql']:
                continue
            # 搜索仍可在 WHERE 中使用错误字段，但返回列不应读取任何详情正文。
            selected = query['sql'].split(' FROM ')[0].lower()
            for field in ('execution_logs', 'error_message', 'screenshots', 'description',
                          'variables', 'password', 'suite_execution_id'):
                self.assertNotIn(f'"{field}"', selected)

    def test_list_returns_summary_without_large_fields(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 3)
        self.assert_lightweight_queries(queries)
        row = response.data['results'][0]
        self.assertEqual(set(row), {
            'id', 'test_case', 'test_case_name', 'project', 'project_name',
            'test_suite', 'test_suite_name', 'execution_source', 'status', 'engine',
            'browser', 'headless', 'execution_time', 'started_at', 'finished_at',
            'created_by', 'created_by_name', 'created_at',
        })
        self.assertEqual(row['test_case'], self.case.pk)
        self.assertEqual(row['test_case_name'], self.case.name)
        self.assertEqual(row['project_name'], self.project.name)
        self.assertEqual(row['test_suite_name'], self.suite.name)
        self.assertEqual(row['created_by_name'], self.owner.username)
        self.assertTrue(row['headless'])
        self.assertEqual(row['execution_time'], 2.5)

    def test_query_count_stays_fixed_and_null_suite_is_supported(self):
        with CaptureQueriesContext(connection) as before:
            self.client.get(self.url, self.params)
        TestCaseExecution.objects.bulk_create([
            TestCaseExecution(project=self.project, test_case=self.case, created_by=self.owner)
            for _ in range(30)
        ])
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(self.url, self.params)
        self.assertEqual(response.data['count'], 31)
        self.assertEqual(len(response.data['results']), 31)
        self.assertEqual(len(after), len(before))
        self.assert_lightweight_queries(after)
        row = response.data['results'][0]
        self.assertIsNone(row['test_suite'])
        self.assertIsNone(row['test_suite_name'])

    def test_detail_keeps_logs_errors_and_screenshots(self):
        response = self.client.get(f'{self.url}{self.execution.pk}/')
        self.assertEqual(response.status_code, 200)
        for field in ('execution_logs', 'error_message', 'screenshots'):
            self.assertEqual(response.data[field], getattr(self.execution, field))

    def test_filters_search_and_stable_pagination(self):
        newer = TestCaseExecution.objects.create(
            project=self.project, test_case=self.case, created_by=self.owner, status='passed',
        )
        TestCaseExecution.objects.filter(project=self.project).update(created_at=timezone.now())
        params = {**self.params, 'page_size': 1}
        first = self.client.get(self.url, params)
        second = self.client.get(self.url, {**params, 'page': 2})
        self.assertEqual(first.data['results'][0]['id'], newer.pk)
        self.assertEqual(second.data['results'][0]['id'], self.execution.pk)
        self.assertIsNotNone(first.data['next'])
        self.assertIsNone(second.data['next'])
        with CaptureQueriesContext(connection) as queries:
            filtered = self.client.get(self.url, {
                **self.params, 'search': 'checkout failure', 'status': 'failed',
                'test_suite': self.suite.pk, 'test_case': self.case.pk, 'execution_source': 'suite',
            })
        self.assertEqual(filtered.data['count'], 1)
        self.assertEqual(filtered.data['results'][0]['id'], self.execution.pk)
        self.assert_lightweight_queries(queries)
        named = self.client.get(self.url, {**self.params, 'search': self.case.name})
        self.assertEqual(named.data['count'], 2)

    def test_project_permissions_and_soft_deletion(self):
        other = get_user_model().objects.create_user(username='case-execution-private')
        private_project = UiProject.objects.create(name='Private', owner=other)
        private_case = UiTestCase.objects.create(project=private_project, name='Private', created_by=other)
        private = TestCaseExecution.objects.create(
            project=private_project, test_case=private_case, created_by=other,
        )
        deleted = UiProject.objects.create(name='Deleted', owner=self.owner, is_deleted=True)
        deleted_case = UiTestCase.objects.create(project=deleted, name='Deleted', created_by=self.owner)
        TestCaseExecution.objects.create(project=deleted, test_case=deleted_case, created_by=self.owner)
        self.assertEqual(self.client.get(self.url).data['count'], 1)
        self.assertEqual(self.client.get(f'{self.url}{private.pk}/').status_code, 404)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.url, self.params).data['count'], 1)
        self.assertEqual(self.client.get(f'{self.url}{self.execution.pk}/').status_code, 200)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(self.url, self.params).data['results'], [])
        self.assertEqual(self.client.get(f'{self.url}{self.execution.pk}/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
