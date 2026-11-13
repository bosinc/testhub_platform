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

    def test_tree_uses_one_query_without_loading_scripts_or_project_details(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'project': self.project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 1)
        self.assertNotIn('ui_script_element_usages', queries[0]['sql'])
        self.assertNotIn('ui_test_scripts', queries[0]['sql'])
        self.assertEqual(response.data, [{
            'id': self.element.pk, 'name': 'Submit', 'type': 'element', 'element_type': 'BUTTON',
            'locator_strategy': 'role', 'locator_value': 'button',
            'locator_options': {'name': 'Submit', 'exact': True},
            'validation_status': 'UNKNOWN', 'usage_count': 2, 'order': 2,
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
        self.assertEqual(response.data[0]['usage_count'], 0)
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
