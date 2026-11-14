from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ui_automation.models import UiProject


class UiProjectApiPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username='ui-project-user', email='owner@example.test')
        cls.member = user_model.objects.create_user(username='ui-project-member', email='member@example.test')
        cls.projects = [
            UiProject.objects.create(
                name=f'UI project {index}',
                description='Project description',
                base_url='https://example.test',
                owner=cls.user,
                variables=[{'name': 'token', 'value': 'secret-value'}],
            )
            for index in range(25)
        ]
        cls.projects[0].members.add(cls.member)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_uses_compact_fields_and_has_no_per_project_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('uiproject-list'), {'page_size': 100})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 20)
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(len(queries), 2)
        first = response.data['results'][0]
        self.assertEqual(first['owner'], {
            'id': self.user.pk,
            'username': self.user.username,
        })
        self.assertEqual(first['workspace_project'], self.projects[0].workspace_project_id)
        self.assertNotIn('members', first)
        self.assertNotIn('variables', first)

    def test_detail_keeps_full_project_fields(self):
        project = self.projects[0]
        response = self.client.get(reverse('uiproject-detail', args=[project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('members', response.data)
        self.assertEqual(response.data['variables'], project.variables)
        self.assertEqual(response.data['workspace_project'], project.workspace_project_id)
