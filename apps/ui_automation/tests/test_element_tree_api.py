from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ui_automation.models import (
    Element, ElementGroup, LocatorStrategy, ScriptElementUsage,
    TestCase as UiTestCase, TestCaseStep, TestScript, UiProject,
)


class ElementTreeApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='element-tree-user')
        cls.other_user = get_user_model().objects.create_user(username='element-tree-other')
        cls.project = UiProject.objects.create(
            name='Element tree', base_url='https://example.test', owner=cls.user,
        )
        cls.strategy = LocatorStrategy.objects.create(name='role')
        cls.group = ElementGroup.objects.create(project=cls.project, name='Product')
        cls.element = Element.objects.create(
            project=cls.project, group=cls.group, name='Submit', page='Product',
            locator_strategy=cls.strategy, locator_value='button',
            locator_options={'name': 'Submit', 'exact': True}, order=2,
            usage_count=99,
            description='Submit the product form',
        )
        case = UiTestCase.objects.create(project=cls.project, created_by=cls.user, name='Create product')
        TestCaseStep.objects.bulk_create([
            TestCaseStep(test_case=case, element=cls.element, step_number=index, action_type='click')
            for index in (1, 2)
        ])
        script = TestScript.objects.create(project=cls.project, name='Recorded script', content='pass\n' * 1000)
        ScriptElementUsage.objects.create(
            script=script, element=cls.element, line_number=1, usage_type='CLICK',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('element-tree')

    def assert_lightweight_queries(self, queries):
        """列表和树不得因为详情统计或嵌套关系增加关联查询。"""
        for query in queries:
            sql = query['sql'].lower()
            for table in ('ui_test_case_steps', 'ui_script_element_usages', 'ui_test_scripts',
                          'ui_element_groups', 'users_user'):
                self.assertNotIn(table, sql)
            self.assertNotIn('group by', sql)

    def test_tree_uses_one_query_without_loading_scripts_or_project_details(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 1)
        self.assertNotIn('ui_script_element_usages', queries[0]['sql'])
        self.assertNotIn('ui_test_scripts', queries[0]['sql'])
        self.assert_lightweight_queries(queries)
        self.assertEqual(response.data, [{
            'id': self.element.pk, 'name': 'Submit', 'type': 'element', 'element_type': 'BUTTON',
            'locator_strategy': 'role', 'locator_value': 'button',
            'locator_options': {'name': 'Submit', 'exact': True},
            'validation_status': 'UNKNOWN', 'order': 2,
            'group_id': self.group.pk, 'page': 'Product', 'children': [],
        }])

    def test_tree_is_not_paginated_and_query_count_does_not_grow(self):
        Element.objects.bulk_create([
            Element(project=self.project, name=f'Extra {index:03}',
                    locator_strategy=self.strategy, locator_value='button', page='Another page')
            for index in range(100)
        ])
        with self.assertNumQueries(1):
            response = self.client.get(self.url, {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 101)
        self.assertEqual(response.data[0]['name'], 'Extra 000')
        self.assertNotIn('usage_count', response.data[0])
        self.assertEqual(response.data[-1]['id'], self.element.pk)

    def test_tree_preserves_owner_member_and_soft_delete_permissions(self):
        self.client.force_authenticate(user=self.other_user)
        self.assertEqual(self.client.get(self.url, {'project': self.project.pk}).data, [])
        self.project.members.add(self.other_user)
        response = self.client.get(self.url, {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['id'], self.element.pk)
        self.project.is_deleted = True
        self.project.save(update_fields=['is_deleted'])
        self.assertEqual(self.client.get(self.url, {'project': self.project.pk}).data, [])

    def test_tree_requires_project_and_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url, {'project': self.project.pk}).status_code, 401)

    def test_list_defaults_to_flat_fields(self):
        url = reverse('element-list')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, {'project': self.project.pk, 'page_size': 1000})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 3)
        self.assert_lightweight_queries(queries)
        item = response.data['results'][0]
        self.assertEqual(set(item), {
            'id', 'name', 'element_type', 'locator_strategy', 'locator_value',
            'locator_options', 'page', 'group_id', 'description', 'created_at', 'updated_at',
        })
        self.assertEqual(item['locator_strategy'], 'role')
        self.assertEqual(item['group_id'], self.group.pk)
        self.assertEqual(item['description'], self.element.description)

        # 扩大列表后查询数仍固定，避免 only 字段遗漏造成逐行延迟加载。
        Element.objects.bulk_create([
            Element(project=self.project, name=f'Extra {index:03}',
                    locator_strategy=self.strategy, locator_value='button')
            for index in range(30)
        ])
        with CaptureQueriesContext(connection) as many_queries:
            response = self.client.get(url, {'project': self.project.pk, 'page_size': 1000})
        self.assertEqual(response.data['count'], 31)
        self.assertEqual(len(many_queries), len(queries))
        self.assert_lightweight_queries(many_queries)

    def test_list_preserves_filters_pagination_order_and_permissions(self):
        url = reverse('element-list')
        extra = Element.objects.create(
            project=self.project, group=self.group, name='Submit', page='Product',
            locator_strategy=self.strategy, locator_value='other', order=2,
        )
        params = {'project': self.project.pk, 'page_name': 'Product', 'search': 'Submit',
                  'group': self.group.pk, 'locator_strategy': self.strategy.pk, 'page_size': 1}
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['results'][0]['id'], self.element.pk)
        self.assertIsNotNone(response.data['next'])
        second = self.client.get(url, {**params, 'page': 2})
        self.assertEqual(second.data['results'][0]['id'], extra.pk)
        self.assertIsNone(second.data['next'])
        self.assertEqual(self.client.get(url, {**params, 'page_name': 'Missing'}).data['count'], 0)

        self.client.force_authenticate(user=self.other_user)
        self.assertEqual(self.client.get(url, params).data['results'], [])
        self.project.members.add(self.other_user)
        self.assertEqual(self.client.get(url, params).data['count'], 2)
        self.project.is_deleted = True
        self.project.save(update_fields=['is_deleted'])
        self.assertEqual(self.client.get(url).data['results'], [])
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_detail_keeps_full_fields_and_real_case_reference_count(self):
        response = self.client.get(reverse('element-detail', args=[self.element.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['usage_count'], 2)
        self.assertEqual(response.data['locator_strategy']['name'], 'role')
        self.assertEqual(response.data['project']['id'], self.project.pk)
        self.assertEqual(response.data['description'], self.element.description)
        self.assertEqual(len(response.data['usage_scripts']), 1)
        self.assertIn('backup_locators', response.data)
        self.assertIn('all_locators', response.data)

    def test_suggestions_still_compute_reference_count_on_demand(self):
        response = self.client.post(reverse('element-generate-suggestions', args=[self.element.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'suggestions': ['建议验证按钮点击后的页面跳转或状态变化']})
        TestCaseStep.objects.filter(element=self.element).delete()
        response = self.client.post(reverse('element-generate-suggestions', args=[self.element.pk]))
        self.assertIn('此元素尚未被任何正式用例步骤引用，考虑是否需要删除', response.data['suggestions'])
