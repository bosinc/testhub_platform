"""Scenario tree endpoint and scenario list pagination/query-count tests."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from ..models import ApiProject, ScenarioCase, ScenarioCaseFolder

User = get_user_model()

TREE_URL = '/api/api-testing/scenario-cases/tree/'
LIST_URL = '/api/api-testing/scenario-cases/'
FOLDER_LIST_URL = '/api/api-testing/scenario-case-folders/'


class ScenarioTreeFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tree-owner', password='password')
        self.other_user = User.objects.create_user(username='tree-other', password='password')
        self.project = ApiProject.objects.create(
            name='接口项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.second_project = ApiProject.objects.create(
            name='第二个项目', project_type='HTTP', status='IN_PROGRESS', owner=self.user)
        self.foreign_project = ApiProject.objects.create(
            name='他人项目', project_type='HTTP', status='IN_PROGRESS', owner=self.other_user)

        self.folder = ScenarioCaseFolder.objects.create(project=self.project, name='登录')
        self.empty_folder = ScenarioCaseFolder.objects.create(
            project=self.project, parent=self.folder, name='空目录')
        self.second_folder = ScenarioCaseFolder.objects.create(
            project=self.second_project, name='订单')
        self.foreign_folder = ScenarioCaseFolder.objects.create(
            project=self.foreign_project, name='他人目录')

        self.scenario = ScenarioCase.objects.create(
            project=self.project, name='登录成功', description='描述', folder=self.folder,
            variables={'token': 'x'}, created_by=self.user)
        self.ungrouped = ScenarioCase.objects.create(
            project=self.project, name='未分组场景', created_by=self.user)
        self.second_scenario = ScenarioCase.objects.create(
            project=self.second_project, name='下单', folder=self.second_folder,
            created_by=self.user)
        self.foreign_scenario = ScenarioCase.objects.create(
            project=self.foreign_project, name='他人场景', folder=self.foreign_folder,
            created_by=self.other_user)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def add_scenarios(self, count):
        # 创建人各不相同，才能暴露逐行查询用户的问题。
        for index in range(count):
            creator = User.objects.create_user(username=f'tree-creator-{index}', password='password')
            ScenarioCase.objects.create(
                project=self.project, name=f'批量场景{index}', folder=self.folder, created_by=creator)


class ScenarioTreeApiTests(ScenarioTreeFixture):
    def test_returns_only_lightweight_fields_for_the_project(self):
        response = self.client.get(TREE_URL, {'project': self.project.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'folders', 'scenario_cases'})
        scenarios = {item['id']: item for item in response.data['scenario_cases']}
        self.assertEqual(set(scenarios), {self.scenario.id, self.ungrouped.id})
        self.assertEqual(dict(scenarios[self.scenario.id]), {
            'id': self.scenario.id, 'name': '登录成功', 'folder': self.folder.id,
            'project': self.project.id, 'environment': None,
        })
        self.assertIsNone(scenarios[self.ungrouped.id]['folder'])

    def test_includes_folders_without_scenarios(self):
        response = self.client.get(TREE_URL, {'project': self.project.id})

        folders = {item['id']: item for item in response.data['folders']}
        self.assertEqual(set(folders), {self.folder.id, self.empty_folder.id})
        self.assertEqual(dict(folders[self.empty_folder.id]), {
            'id': self.empty_folder.id, 'project': self.project.id,
            'parent': self.folder.id, 'name': '空目录',
        })

    def test_without_project_returns_every_accessible_project_only(self):
        response = self.client.get(TREE_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.data['scenario_cases']},
            {self.scenario.id, self.ungrouped.id, self.second_scenario.id})
        self.assertEqual(
            {item['id'] for item in response.data['folders']},
            {self.folder.id, self.empty_folder.id, self.second_folder.id})

    def test_inaccessible_project_returns_nothing(self):
        response = self.client.get(TREE_URL, {'project': self.foreign_project.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['scenario_cases'], [])
        self.assertEqual(response.data['folders'], [])

    def test_member_can_load_the_tree(self):
        self.project.members.add(self.other_user)
        client = APIClient()
        client.force_authenticate(user=self.other_user)

        response = client.get(TREE_URL, {'project': self.project.id})

        self.assertEqual(
            {item['id'] for item in response.data['scenario_cases']},
            {self.scenario.id, self.ungrouped.id})

    def test_requires_authentication(self):
        response = APIClient().get(TREE_URL, {'project': self.project.id})

        self.assertIn(response.status_code, (401, 403))

    def test_query_count_does_not_grow_with_scenarios(self):
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(TREE_URL, {'project': self.project.id})
        self.add_scenarios(15)

        with CaptureQueriesContext(connection) as grown:
            response = self.client.get(TREE_URL, {'project': self.project.id})

        self.assertEqual(len(response.data['scenario_cases']), 17)
        self.assertEqual(len(grown), len(baseline))


class ScenarioCaseListApiTests(ScenarioTreeFixture):
    def test_page_size_parameter_is_honoured(self):
        self.add_scenarios(25)

        response = self.client.get(LIST_URL, {'project': self.project.id, 'page_size': 1500})

        self.assertEqual(response.data['count'], 27)
        self.assertEqual(len(response.data['results']), 27)
        self.assertIsNone(response.data['next'])

    def test_default_page_size_is_unchanged(self):
        self.add_scenarios(25)

        response = self.client.get(LIST_URL, {'project': self.project.id})

        self.assertEqual(len(response.data['results']), 20)
        self.assertIsNotNone(response.data['next'])

    def test_list_keeps_detail_fields_and_query_count_is_constant(self):
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(LIST_URL, {'project': self.project.id, 'page_size': 1500})
        self.add_scenarios(15)

        with CaptureQueriesContext(connection) as grown:
            response = self.client.get(LIST_URL, {'project': self.project.id, 'page_size': 1500})

        self.assertEqual(len(grown), len(baseline))
        row = next(item for item in response.data['results'] if item['id'] == self.scenario.id)
        self.assertEqual(row['description'], '描述')
        self.assertEqual(row['created_by']['username'], 'tree-owner')

    def test_detail_returns_fields_missing_from_the_tree(self):
        response = self.client.get(f'{LIST_URL}{self.scenario.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['description'], '描述')
        self.assertEqual(response.data['created_by']['username'], 'tree-owner')
        self.assertIn('updated_at', response.data)

    def test_folder_list_honours_page_size(self):
        for index in range(25):
            ScenarioCaseFolder.objects.create(project=self.project, name=f'目录{index}')

        response = self.client.get(FOLDER_LIST_URL, {'project': self.project.id, 'page_size': 1500})

        self.assertEqual(len(response.data['results']), 27)
        self.assertIsNone(response.data['next'])
