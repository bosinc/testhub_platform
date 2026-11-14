from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ui_automation.models import (
    TestCase as UiTestCase, TestExecution, TestScript, TestSuite,
    TestSuiteScript, TestSuiteTestCase, UiProject,
)


class SuiteExecutionListApiTests(TestCase):
    """套件与执行记录列表不展开详情，数量增长也不增加查询次数。"""

    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username='summary-owner')
        cls.member = get_user_model().objects.create_user(username='summary-member')
        cls.project = UiProject.objects.create(name='UI project', owner=cls.owner)
        cls.project.members.add(cls.member)
        cls.suite = TestSuite.objects.create(
            project=cls.project, name='Checkout', description='Payment flows',
            execution_status='failed', passed_count=2, failed_count=1,
        )
        cls.script = TestScript.objects.create(
            project=cls.project, name='Checkout script', content='print("large script")\n' * 1000,
        )
        other_script = TestScript.objects.create(project=cls.project, name='Other script', content='pass')
        TestSuiteScript.objects.bulk_create([
            TestSuiteScript(test_suite=cls.suite, test_script=cls.script, order=0),
            TestSuiteScript(test_suite=cls.suite, test_script=other_script, order=1),
        ])
        cases = UiTestCase.objects.bulk_create([
            UiTestCase(project=cls.project, created_by=cls.owner, name=f'Case {index}')
            for index in range(3)
        ])
        TestSuiteTestCase.objects.bulk_create([
            TestSuiteTestCase(test_suite=cls.suite, test_case=case, order=index)
            for index, case in enumerate(cases)
        ])
        cls.execution = TestExecution.objects.create(
            project=cls.project, test_suite=cls.suite, test_script=cls.script,
            executed_by=cls.owner, status='FAILED', environment='FIREFOX', browser='firefox',
            total_cases=4, passed_cases=2, failed_cases=1, skipped_cases=1, duration=2.5,
            execution_logs='execution log\n' * 1000, error_message='failure details\n' * 1000,
            result_data={'test_cases': [{'name': 'Case 0', 'logs': 'step log\n' * 1000}]},
            run_config={'private': 'config'}, artifact_manifest=[{'path': 'trace.zip'}],
            report_url='/media/report.html',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.suite_url = reverse('testsuite-list')
        # API 测试模块注册了同名路由，执行接口使用明确的 UI 路径避免反向解析到其他模块。
        self.execution_url = '/api/ui-automation/test-executions/'
        self.params = {'project': self.project.pk}

    def assert_lightweight_execution_queries(self, queries):
        for query in queries:
            # 除去 WHERE 中的过滤条件，返回列不能包含任何日志、结果或嵌套正文。
            selected = query['sql'].split(' FROM ')[0].lower()
            # project 过滤器会单独校验项目，重点检查执行记录查询的返回列。
            if 'FROM "ui_test_executions"' in query['sql']:
                for field in ('execution_logs', 'result_data', 'run_config', 'artifact_manifest',
                              'error_message', 'content', 'variables', 'password'):
                    self.assertNotIn(f'"{field}"', selected)
            for table in ('ui_test_suite_test_cases', 'ui_test_suite_scripts', 'ui_test_cases'):
                self.assertNotIn(table, query['sql'])

    def test_suite_list_returns_flat_summary_and_count_without_loading_relations(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.suite_url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 3)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(set(row), {
            'id', 'project', 'project_name', 'name', 'description', 'test_case_count',
            'execution_status', 'passed_count', 'failed_count', 'created_at', 'updated_at',
        })
        self.assertEqual(row['project'], self.project.pk)
        self.assertEqual(row['project_name'], self.project.name)
        self.assertEqual(row['test_case_count'], 3)
        self.assertEqual(row['passed_count'], 2)
        self.assertEqual(row['failed_count'], 1)
        for query in queries:
            for table in ('ui_test_scripts', 'ui_test_cases', 'ui_test_suite_scripts', 'users_user'):
                self.assertNotIn(table, query['sql'])

    def test_suite_query_count_stays_fixed_and_empty_suite_counts_zero(self):
        with CaptureQueriesContext(connection) as before:
            self.client.get(self.suite_url, self.params)
        TestSuite.objects.bulk_create([
            TestSuite(project=self.project, name=f'Empty suite {index:03}') for index in range(25)
        ])
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(self.suite_url, self.params)
        self.assertEqual(response.data['count'], 26)
        self.assertEqual(len(after), len(before))
        self.assertIsNotNone(response.data['next'])
        self.assertTrue(all(row['test_case_count'] == 0 for row in response.data['results']))

    def test_suite_detail_and_selected_cases_remain_available(self):
        response = self.client.get(reverse('testsuite-detail', args=[self.suite.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['project']['id'], self.project.pk)
        self.assertEqual(response.data['suite_scripts'][0]['test_script']['content'], self.script.content)
        with CaptureQueriesContext(connection) as queries:
            selected = self.client.get(reverse('testsuite-test-cases', args=[self.suite.pk]))
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(len(selected.data), 3)
        self.assertEqual([row['order'] for row in selected.data], [0, 1, 2])
        self.assertEqual(len(queries), 2)

    def test_execution_list_omits_large_fields_and_returns_display_statistics(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.execution_url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 3)
        self.assert_lightweight_execution_queries(queries)
        row = response.data['results'][0]
        for name in ('execution_logs', 'result_data', 'error_message', 'run_config', 'artifact_manifest'):
            self.assertNotIn(name, row)
        self.assertEqual(row['project'], self.project.pk)
        self.assertEqual(row['test_suite'], self.suite.pk)
        self.assertEqual(row['test_script'], self.script.pk)
        self.assertEqual(row['executed_by'], self.owner.pk)
        self.assertEqual(row['test_suite_name'], self.suite.name)
        self.assertEqual(row['test_script_name'], self.script.name)
        self.assertEqual(row['executed_by_name'], self.owner.username)
        self.assertEqual(row['pass_rate'], 50)
        self.assertEqual(row['skipped_cases'], 1)
        self.assertEqual(row['report_url'], '/media/report.html')

    def test_execution_query_count_stays_fixed_and_nullable_links_are_handled(self):
        with CaptureQueriesContext(connection) as before:
            self.client.get(self.execution_url, self.params)
        TestExecution.objects.bulk_create([
            TestExecution(project=self.project, status='PENDING') for _ in range(30)
        ])
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(self.execution_url, {**self.params, 'page_size': 1000})
        self.assertEqual(response.data['count'], 31)
        self.assertEqual(len(before), len(after))
        self.assert_lightweight_execution_queries(after)
        row = response.data['results'][0]
        self.assertIsNone(row['test_suite'])
        self.assertIsNone(row['test_script'])
        self.assertIsNone(row['executed_by'])
        self.assertEqual(row['test_suite_name'], '-')
        self.assertEqual(row['test_script_name'], '-')
        self.assertEqual(row['executed_by_name'], '-')
        self.assertEqual(row['pass_rate'], 0)

    def test_execution_detail_and_logs_keep_full_data(self):
        response = self.client.get(f'{self.execution_url}{self.execution.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['execution_logs'], self.execution.execution_logs)
        self.assertEqual(response.data['result_data'], self.execution.result_data)
        self.assertEqual(response.data['run_config'], self.execution.run_config)
        self.assertEqual(response.data['artifact_manifest'], self.execution.artifact_manifest)
        self.assertEqual(response.data['error_message'], self.execution.error_message)
        self.assertEqual(response.data['test_script']['content'], self.script.content)
        self.assertEqual(response.data['test_suite']['test_case_count'], 3)
        logs = self.client.get(f'{self.execution_url}{self.execution.pk}/logs/')
        self.assertEqual(logs.data['logs'], self.execution.execution_logs)

    def test_execution_filters_search_and_pagination_keep_working(self):
        newer = TestExecution.objects.create(project=self.project, status='SUCCESS')
        TestExecution.objects.filter(project=self.project).update(created_at=timezone.now())
        params = {**self.params, 'page_size': 1}
        first = self.client.get(self.execution_url, params)
        second = self.client.get(self.execution_url, {**params, 'page': 2})
        self.assertEqual(first.data['results'][0]['id'], newer.pk)
        self.assertEqual(second.data['results'][0]['id'], self.execution.pk)
        self.assertIsNotNone(first.data['next'])
        self.assertIsNone(second.data['next'])
        filtered = self.client.get(self.execution_url, {
            **self.params, 'search': 'failure details', 'status': 'FAILED',
            'test_suite': self.suite.pk, 'test_script': self.script.pk,
            'executed_by': self.owner.pk, 'environment': 'FIREFOX',
        })
        self.assertEqual(filtered.data['count'], 1)
        self.assertEqual(filtered.data['results'][0]['id'], self.execution.pk)

    def test_suite_search_and_stable_order(self):
        newer = TestSuite.objects.create(project=self.project, name='Another suite')
        TestSuite.objects.filter(project=self.project).update(created_at=timezone.now())
        response = self.client.get(self.suite_url, self.params)
        self.assertEqual([row['id'] for row in response.data['results']], [newer.pk, self.suite.pk])
        filtered = self.client.get(self.suite_url, {**self.params, 'search': 'Payment flows'})
        self.assertEqual(filtered.data['count'], 1)
        self.assertEqual(filtered.data['results'][0]['test_case_count'], 3)

    def test_both_lists_preserve_project_permissions_and_soft_deletion(self):
        other = get_user_model().objects.create_user(username='summary-private')
        private_project = UiProject.objects.create(name='Private', owner=other)
        TestSuite.objects.create(project=private_project, name='Private suite')
        TestExecution.objects.create(project=private_project)
        deleted = UiProject.objects.create(name='Deleted', owner=self.owner, is_deleted=True)
        TestSuite.objects.create(project=deleted, name='Deleted suite')
        TestExecution.objects.create(project=deleted)
        for url in (self.suite_url, self.execution_url):
            self.assertEqual(self.client.get(url).data['count'], 1)
            self.assertEqual(self.client.get(url, {'project': private_project.pk}).data['results'], [])
            self.client.force_authenticate(self.member)
            self.assertEqual(self.client.get(url, self.params).data['count'], 1)
            self.client.force_authenticate(other)
            self.assertEqual(self.client.get(url, self.params).data['results'], [])
            self.client.force_authenticate(None)
            self.assertEqual(self.client.get(url).status_code, 401)
            self.client.force_authenticate(self.owner)

    def test_script_history_uses_lightweight_response(self):
        response = self.client.get(reverse('testscript-script-executions', args=[self.script.pk]))
        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['id'], self.execution.pk)
        self.assertEqual(row['test_script_name'], self.script.name)
        self.assertNotIn('execution_logs', row)
        self.assertNotIn('result_data', row)
