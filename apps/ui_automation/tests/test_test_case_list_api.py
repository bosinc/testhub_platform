from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ui_automation.models import (
    Element, LocatorStrategy, TestCase as UiTestCase, TestCaseFolder,
    TestCaseStep, UiProject,
)


class TestCaseListApiTests(TestCase):
    """用例列表只加载摘要，统计、权限及详情步骤保持正确。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='case-list-owner')
        cls.other = get_user_model().objects.create_user(username='case-list-other')
        cls.project = UiProject.objects.create(name='UI project', owner=cls.user)
        cls.folder = TestCaseFolder.objects.create(project=cls.project, name='Checkout')
        strategy = LocatorStrategy.objects.create(name='role')
        cls.element = Element.objects.create(
            project=cls.project, name='Submit', locator_strategy=strategy,
            locator_value='button', locator_options={'name': 'Submit'}, page='Checkout',
        )
        cls.case = UiTestCase.objects.create(
            project=cls.project, created_by=cls.user, folder=cls.folder,
            name='Submit order', description='Order form', status='ready', priority='high',
        )
        cls.empty_case = UiTestCase.objects.create(
            project=cls.project, created_by=cls.user, name='Empty case',
        )
        # 同一元素的重复引用和无元素步骤都必须计入步骤数，不能按元素去重。
        TestCaseStep.objects.bulk_create([
            TestCaseStep(test_case=cls.case, element=cls.element, step_number=2,
                         action_type='click', description='Submit again'),
            TestCaseStep(test_case=cls.case, element=cls.element, step_number=1,
                         action_type='click', description='Submit'),
            TestCaseStep(test_case=cls.case, step_number=3, action_type='wait', input_value='1'),
        ])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse('testcase-list')
        self.params = {'project': self.project.pk, 'page': 1, 'page_size': 1000}

    def test_list_returns_summary_and_exact_step_counts(self):
        response = self.client.get(self.url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        by_id = {item['id']: item for item in response.data['results']}
        item = by_id[self.case.pk]
        self.assertEqual(set(item), {
            'id', 'name', 'description', 'folder', 'project', 'project_name', 'status', 'priority',
            'created_by', 'created_by_name', 'created_at', 'updated_at', 'steps_count',
        })
        self.assertEqual(item['steps_count'], 3)
        self.assertEqual(by_id[self.empty_case.pk]['steps_count'], 0)
        self.assertEqual(item['folder'], self.folder.pk)
        self.assertEqual(item['project_name'], self.project.name)
        self.assertEqual(item['created_by_name'], self.user.username)

    def test_query_count_is_bounded_without_fetching_steps_or_elements(self):
        with CaptureQueriesContext(connection) as small_queries:
            self.client.get(self.url, self.params)
        extra_cases = UiTestCase.objects.bulk_create([
            UiTestCase(project=self.project, created_by=self.user, name=f'Case {index:03}')
            for index in range(30)
        ])
        TestCaseStep.objects.bulk_create([
            TestCaseStep(test_case=case, step_number=number, action_type='wait', input_value='1')
            for case in extra_cases for number in range(1, 11)
        ])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 32)
        self.assertEqual(len(queries), len(small_queries))
        self.assertEqual(len(queries), 3)
        for item in response.data['results']:
            if item['id'] in {case.pk for case in extra_cases}:
                self.assertEqual(item['steps_count'], 10)
        for query in queries:
            # 步骤表只参与数据库计数，不读取步骤正文或元素定位信息。
            for excluded in ('ui_elements', 'locator_strategies', 'ui_test_case_folders',
                             'action_params', 'input_value', 'testcase_automation_links'):
                self.assertNotIn(excluded, query['sql'])

    def test_list_filters_pagination_and_stable_default_order(self):
        UiTestCase.objects.filter(project=self.project).update(created_at=timezone.now())
        first = self.client.get(self.url, {**self.params, 'page_size': 1})
        self.assertEqual(first.data['results'][0]['id'], self.empty_case.pk)
        self.assertIsNotNone(first.data['next'])
        second = self.client.get(self.url, {**self.params, 'page_size': 1, 'page': 2})
        self.assertEqual(second.data['results'][0]['id'], self.case.pk)
        self.assertIsNone(second.data['next'])
        filtered = self.client.get(self.url, {
            **self.params, 'search': 'Order form', 'status': 'ready', 'priority': 'high',
            'created_by': self.user.pk, 'ordering': 'name',
        })
        self.assertEqual(filtered.data['count'], 1)
        self.assertEqual(filtered.data['results'][0]['steps_count'], 3)
        self.assertEqual(self.client.get(self.url, {**self.params, 'status': 'failed'}).data['count'], 0)

    def test_list_preserves_owner_member_soft_delete_and_authentication(self):
        private_project = UiProject.objects.create(name='Private', owner=self.other)
        UiTestCase.objects.create(project=private_project, created_by=self.other, name='Private case')
        self.assertEqual(self.client.get(self.url).data['count'], 2)
        self.assertEqual(self.client.get(self.url, {'project': private_project.pk}).data['results'], [])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url, self.params).data['results'], [])
        self.project.members.add(self.other)
        self.assertEqual(self.client.get(self.url, self.params).data['count'], 2)
        self.project.is_deleted = True
        self.project.save(update_fields=['is_deleted'])
        self.assertEqual(self.client.get(self.url).data['count'], 1)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url, self.params).status_code, 401)

    def test_detail_keeps_ordered_steps_and_locator_information(self):
        response = self.client.get(reverse('testcase-detail', args=[self.case.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([step['step_number'] for step in response.data['steps']], [1, 2, 3])
        first = response.data['steps'][0]
        self.assertEqual(first['element_name'], 'Submit')
        self.assertEqual(first['element_locator_strategy'], 'role')
        self.assertEqual(first['element_locator_options'], {'name': 'Submit'})
        self.assertEqual(response.data['linked_test_cases'], [])

    def test_write_and_copy_responses_keep_steps_and_list_count_updates(self):
        response = self.client.post(self.url, {
            'project': self.project.pk, 'name': 'New case',
            'steps': [{'action_type': 'wait', 'input_value': '1'}],
        }, format='json')
        self.assertEqual(response.status_code, 201)
        case_id = response.data['id']
        self.assertEqual(len(response.data['steps']), 1)
        detail_url = reverse('testcase-detail', args=[case_id])
        updated = self.client.patch(detail_url, {'steps': []}, format='json')
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['steps'], [])
        copied = self.client.post(reverse('testcase-copy-case', args=[self.case.pk]))
        self.assertEqual(copied.status_code, 201)
        self.assertEqual(len(copied.data['steps']), 3)
        counts = {item['id']: item['steps_count'] for item in self.client.get(self.url, self.params).data['results']}
        self.assertEqual(counts[case_id], 0)
        self.assertEqual(counts[copied.data['id']], 3)
