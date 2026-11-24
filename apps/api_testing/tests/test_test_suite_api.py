"""Test suite list/detail payload and query-count tests."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from ..models import ApiProject, Environment, ScenarioCase, TestSuite, TestSuiteItem

User = get_user_model()

LIST_URL = '/api/api-testing/test-suites/'


class TestSuiteListApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='suite-owner', password='password')
        self.project = ApiProject.objects.create(
            name='接口项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.environment = Environment.objects.create(
            name='测试环境', scope='GLOBAL', base_url='http://example.test', created_by=self.user)
        self.suite = self.add_suite('回归套件', 3)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def add_suite(self, name, scenario_count):
        # 套件和用例的创建人各不相同，才能暴露逐行查询用户的问题。
        index = User.objects.count()
        suite = TestSuite.objects.create(
            name=name, project=self.project, environment=self.environment,
            created_by=User.objects.create_user(username=f'suite-creator-{index}', password='password'))
        for order in range(scenario_count):
            creator = User.objects.create_user(
                username=f'case-creator-{index}-{order}', password='password')
            scenario = ScenarioCase.objects.create(
                project=self.project, name=f'{name}-场景{order}', variables={'k': order},
                created_by=creator)
            TestSuiteItem.objects.create(test_suite=suite, scenario_case=scenario, order=order)
        return suite

    def test_list_payload_is_unchanged(self):
        response = self.client.get(LIST_URL, {'project': self.project.id})

        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['id'], self.suite.id)
        self.assertEqual(row['environment'], self.environment.id)
        self.assertEqual(row['environment_name'], '测试环境')
        self.assertEqual(row['created_by']['username'], self.suite.created_by.username)
        # 嵌套用例的顺序与未预取时的关联查询保持一致。
        self.assertEqual(
            [item['id'] for item in row['scenario_cases']],
            list(self.suite.scenario_cases.values_list('id', flat=True)))
        scenario = ScenarioCase.objects.get(pk=row['scenario_cases'][0]['id'])
        self.assertEqual(row['scenario_cases'][0]['name'], scenario.name)
        self.assertEqual(row['scenario_cases'][0]['created_by']['username'], scenario.created_by.username)

    def test_list_query_count_does_not_grow_with_suites_or_scenarios(self):
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(LIST_URL, {'project': self.project.id})
        self.add_suite('冒烟套件', 8)
        self.add_suite('空套件', 0)

        with CaptureQueriesContext(connection) as grown:
            response = self.client.get(LIST_URL, {'project': self.project.id})

        self.assertEqual(len(response.data['results']), 3)
        self.assertEqual(len(grown), len(baseline))

    def test_page_size_parameter_is_honoured(self):
        for index in range(22):
            self.add_suite(f'套件{index}', 0)

        default_page = self.client.get(LIST_URL, {'project': self.project.id})
        full_page = self.client.get(LIST_URL, {'project': self.project.id, 'page_size': 1500})

        # 不传 page_size 时仍是每页 20 条。
        self.assertEqual(len(default_page.data['results']), 20)
        self.assertIsNotNone(default_page.data['next'])
        self.assertEqual(full_page.data['count'], 23)
        self.assertEqual(len(full_page.data['results']), 23)
        self.assertIsNone(full_page.data['next'])

    def test_detail_returns_scenarios(self):
        response = self.client.get(f'{LIST_URL}{self.suite.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['scenario_cases']), 3)
        self.assertEqual(response.data['environment_name'], '测试环境')

    def test_update_response_reflects_new_scenarios(self):
        kept = self.suite.scenario_cases.first()

        response = self.client.put(f'{LIST_URL}{self.suite.id}/', {
            'name': '回归套件', 'project': self.project.id, 'environment': self.environment.id,
            'scenario_case_ids': [kept.id],
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data['scenario_cases']], [kept.id])
        detail = self.client.get(f'{LIST_URL}{self.suite.id}/')
        self.assertEqual([item['id'] for item in detail.data['scenario_cases']], [kept.id])
