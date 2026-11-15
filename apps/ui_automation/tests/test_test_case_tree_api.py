from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ui_automation.models import TestCase as UiTestCase, TestCaseFolder, TestCaseStep, UiProject


class TestCaseTreeApiTests(TestCase):
    """完整用例树保持轻量、权限隔离及固定查询次数。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='case-tree-owner')
        cls.other = get_user_model().objects.create_user(username='case-tree-other')
        cls.project = UiProject.objects.create(name='Tree project', owner=cls.user)
        cls.root = TestCaseFolder.objects.create(project=cls.project, name='Root')
        cls.child = TestCaseFolder.objects.create(project=cls.project, parent=cls.root, name='Child')
        cls.empty = TestCaseFolder.objects.create(project=cls.project, name='Empty')
        cls.case = UiTestCase.objects.create(
            project=cls.project, created_by=cls.user, folder=cls.child,
            name='Login', description='正文不应出现在树中' * 1000, priority='high', status='ready',
        )
        cls.unfiled = UiTestCase.objects.create(project=cls.project, created_by=cls.user, name='Unfiled')
        TestCaseStep.objects.bulk_create([
            TestCaseStep(test_case=cls.case, step_number=index, action_type='wait', input_value='1')
            for index in range(1, 4)
        ])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('testcase-tree')
        self.params = {'project': self.project.pk}

    def nodes(self, tree):
        """按节点 key 索引，测试目录和用例主键重叠时仍能完整保留。"""
        nodes = {}
        pending = list(tree)
        while pending:
            node = pending.pop()
            self.assertNotIn(node['key'], nodes)
            nodes[node['key']] = node
            pending.extend(node.get('children', []))
        return nodes

    def test_tree_returns_nested_folders_unfiled_cases_and_only_summary_fields(self):
        response = self.client.get(self.url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        nodes = self.nodes(response.data)
        self.assertEqual(len(nodes), 5)
        root = nodes[f'folder:{self.root.pk}']
        child = nodes[f'folder:{self.child.pk}']
        case = nodes[f'case:{self.case.pk}']
        self.assertEqual(root['children'], [child])
        self.assertEqual(child['children'], [case])
        self.assertEqual(case, {
            'key': f'case:{self.case.pk}', 'id': self.case.pk, 'type': 'testCase',
            'name': 'Login', 'folder': self.child.pk, 'priority': 'high',
            'status': 'ready', 'steps_count': 3,
        })
        self.assertEqual(set(root), {'key', 'id', 'type', 'name', 'parent', 'children'})
        self.assertEqual(nodes[f'folder:{self.empty.pk}']['children'], [])
        self.assertIn(nodes[f'case:{self.unfiled.pk}'], response.data)
        self.assertEqual(nodes[f'case:{self.unfiled.pk}']['steps_count'], 0)

    def test_tree_uses_two_lightweight_queries_independent_of_size_and_pagination(self):
        with CaptureQueriesContext(connection) as small_queries:
            self.client.get(self.url, self.params)
        # 超过列表接口的最大单页大小，树仍完整返回，且不额外执行分页计数。
        UiTestCase.objects.bulk_create([
            UiTestCase(project=self.project, created_by=self.user, folder=self.child, name=f'Case {index:04}')
            for index in range(1001)
        ])
        TestCaseFolder.objects.bulk_create([
            TestCaseFolder(project=self.project, name=f'Folder {index:03}') for index in range(25)
        ])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {**self.params, 'page_size': 1, 'page': 999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.nodes(response.data)), 1031)
        self.assertEqual(len(queries), len(small_queries))
        self.assertEqual(len(queries), 2)
        for query in queries:
            # 允许步骤计数子查询，禁止读取步骤正文、描述和详情关联。
            sql = query['sql'].lower()
            for excluded in ('description', 'input_value', 'action_params', 'ui_elements',
                             'users_user', 'testcase_automation_links'):
                self.assertNotIn(excluded, sql)

    def test_tree_preserves_owner_member_soft_delete_and_authentication(self):
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(self.url, self.params).data, [])
        self.project.members.add(self.other)
        self.assertEqual(len(self.nodes(self.client.get(self.url, self.params).data)), 5)
        self.project.is_deleted = True
        self.project.save(update_fields=['is_deleted'])
        self.assertEqual(self.client.get(self.url, self.params).data, [])
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url, self.params).status_code, 401)

    def test_tree_requires_valid_project_and_handles_empty_or_missing_projects(self):
        for value in ('', 'abc', '-1', '0', '1.5', '9' * 30):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(self.url, {'project': value}).status_code, 400)
        empty_project = UiProject.objects.create(name='Empty', owner=self.user)
        self.assertEqual(self.client.get(self.url, {'project': empty_project.pk}).data, [])
        self.assertEqual(self.client.get(self.url, {'project': 999999}).data, [])

    def test_tree_orders_folders_before_cases_and_equal_names_by_id(self):
        first = UiTestCase.objects.create(
            project=self.project, created_by=self.user, folder=self.root, name='Same',
        )
        second = UiTestCase.objects.create(
            project=self.project, created_by=self.user, folder=self.root, name='Same',
        )
        tree = self.client.get(self.url, self.params).data
        self.assertEqual([node['key'] for node in tree], [
            f'folder:{self.empty.pk}', f'folder:{self.root.pk}', f'case:{self.unfiled.pk}',
        ])
        root = self.nodes(tree)[f'folder:{self.root.pk}']
        self.assertEqual([node['key'] for node in root['children']], [
            f'folder:{self.child.pk}', f'case:{first.pk}', f'case:{second.pk}',
        ])

    def test_tree_repairs_cycles_without_losing_nodes(self):
        # 模拟历史数据绕过接口校验：父子目录形成环，另一个目录自引用。
        TestCaseFolder.objects.filter(pk=self.root.pk).update(parent=self.child)
        TestCaseFolder.objects.filter(pk=self.empty.pk).update(parent=self.empty)
        response = self.client.get(self.url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.nodes(response.data)), 5)
        self.assertIsNone(self.nodes(response.data)[f'folder:{self.empty.pk}']['parent'])

    def test_cross_project_parent_and_folder_are_not_included_or_exposed(self):
        private_project = UiProject.objects.create(name='Private', owner=self.other)
        private_folder = TestCaseFolder.objects.create(project=private_project, name='Secret folder')
        UiTestCase.objects.create(project=private_project, created_by=self.other, name='Secret case')
        TestCaseFolder.objects.filter(pk=self.root.pk).update(parent=private_folder)
        UiTestCase.objects.filter(pk=self.unfiled.pk).update(folder=private_folder)
        response = self.client.get(self.url, self.params)
        nodes = self.nodes(response.data)
        self.assertEqual(len(nodes), 5)
        self.assertIsNone(nodes[f'folder:{self.root.pk}']['parent'])
        self.assertIsNone(nodes[f'case:{self.unfiled.pk}']['folder'])
        self.assertNotIn('Secret', str(response.data))

    def test_tree_refresh_reflects_case_updates_moves_and_folder_deletion(self):
        case_url = reverse('testcase-detail', args=[self.case.pk])
        response = self.client.patch(case_url, {
            'name': 'Renamed', 'folder': self.empty.pk, 'steps': [],
        }, format='json')
        self.assertEqual(response.status_code, 200)
        nodes = self.nodes(self.client.get(self.url, self.params).data)
        case = nodes[f'case:{self.case.pk}']
        self.assertEqual(case['name'], 'Renamed')
        self.assertEqual(case['folder'], self.empty.pk)
        self.assertEqual(case['steps_count'], 0)
        self.assertEqual(nodes[f'folder:{self.empty.pk}']['children'], [case])
        self.assertEqual(self.client.delete(reverse('test-case-folder-detail', args=[self.empty.pk])).status_code, 204)
        tree = self.client.get(self.url, self.params).data
        nodes = self.nodes(tree)
        self.assertNotIn(f'folder:{self.empty.pk}', nodes)
        self.assertIsNone(nodes[f'case:{self.case.pk}']['folder'])
        self.assertIn(nodes[f'case:{self.case.pk}'], tree)
